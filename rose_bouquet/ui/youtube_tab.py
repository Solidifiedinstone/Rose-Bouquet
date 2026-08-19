"""YouTube itself, with the hostile parts taken out on the way through.

This is not a YouTube client. There is no scraping, no reimplemented feed and
no algorithm of our own: it is YouTube's own desktop site in a web view, with
ads and telemetry removed before the page renders. Sign in and you get
*your* recommendations, subscriptions and history — the real thing, minus the
client that reports on you.

The desktop site rather than the phone one, because the desktop site is the one
with the left rail: Home, Subscriptions, You, your playlists, all where they
belong. YouTube hides that rail behind a hamburger whenever it thinks the
window is narrow, so it is opened for you on every page that can hold it. The
phone site's bottom bar is worth keeping too, so a small one is drawn on top —
ours, three buttons, no page of Google's involved.

Four layers do the work, and they are deliberately separate:

* **A request interceptor**, which never lets an ad or a telemetry beacon leave
  the machine. This is the layer that matters: blocking at the network is not
  defeatable by a page that changes its class names next week.
* **Injected CSS**, which hides what did load — promoted rows, the "turn off
  your ad blocker" dialog. Cosmetic, and expected to rot; the interceptor is
  what keeps the promise.
* **Injected JavaScript**, which skips an ad that got through, dismisses the
  anti-adblock nag, opens the left rail once, and draws the bottom bar.
* **A persistent profile**, so a sign-in survives closing the app, kept in Rose
  Bouquet's own data folder rather than anywhere shared.

The one thing this cannot do is lie about what it is. It is a web view onto
Google's site: signing in means Google knows you signed in. What it does not do
is add anything of its own on top — no analytics, no identifiers, nothing sent
anywhere but YouTube, and every beacon YouTube tries to send is dropped here.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core.library import data_dir
from rose_bouquet.ui.theme import Appearance

logger = logging.getLogger(__name__)

WATCH_URL = "https://www.youtube.com"
MUSIC_URL = "https://music.youtube.com"

#: A real desktop Chrome. Two reasons it is not Qt's own string: Google refuses
#: to sign you in from anything that announces itself as an embedded view
#: ("this browser or app may not be secure"), and the desktop site is only
#: served to something that looks like a desktop browser.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

#: Hosts that exist to serve advertising or to report on you. Blocked outright
#: rather than hidden, so nothing is requested and nothing is measured.
#:
#: `googlevideo.com` is deliberately absent — it serves the actual video, and
#: `accounts.google.com` is absent because that is where signing in happens.
#: Ads and beacons come from the hosts below, and from paths on YouTube's own
#: hosts, which are handled separately further down.
BLOCKED_HOSTS = frozenset({
    "doubleclick.net",
    "googleadservices.com",
    "googlesyndication.com",
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "adservice.google.com",
    "analytics.google.com",
    #: YouTube's own beacon host. Serves nothing but stats — no page on
    #: youtube.com needs a reply from it.
    "s.youtube.com",
    "s2.youtube.com",
})

#: Paths on YouTube's and Google's own hosts that only ever carry advertising
#: or telemetry.
#:
#: `/youtubei/v1/log_event` is the big one: it is the modern watch-and-report
#: endpoint the page calls constantly while you use it. Dropping it costs
#: nothing on screen. `/api/stats/watchtime` is deliberately *not* here —
#: that is what tells a signed-in account where you got to in a video, which
#: is the resume-where-you-left-off you presumably want if you signed in.
BLOCKED_PATHS = (
    "/youtubei/v1/log_event",
    "/api/stats/ads",
    "/api/stats/qoe",
    "/api/stats/atr",
    "/api/stats/delayplay",
    "/pagead/",
    "/ptracking",
    "/generate_204",
    "/gen_204",
    "/csi_204",
    "/error_204",
)

#: Hidden in the page. Cosmetic and expected to need updating — the request
#: interceptor is what actually keeps ads out.
HIDE_CSS = """
/* Ads, on the desktop site and in the player. */
ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ytd-promoted-video-renderer,
ytd-display-ad-renderer, ytd-banner-promo-renderer-background, ytd-banner-promo-renderer,
ytd-statement-banner-renderer, ytd-merch-shelf-renderer, ytd-player-legacy-desktop-watch-ads-renderer,
ytm-companion-slot, ytm-promoted-video-renderer, ytm-promoted-sparkles-web-renderer,
#masthead-ad, #player-ads, .ytp-ad-module, .ytp-ad-overlay-container,
ytd-rich-item-renderer:has(ytd-ad-slot-renderer),
ytd-rich-section-renderer:has(ytd-statement-banner-renderer) { display: none !important; }

/* The "ad blockers are not allowed" dialog, and the sheet it greys the page
   with. Hidden as well as dismissed by script, so it never even flashes. */
ytd-enforcement-message-view-model,
tp-yt-paper-dialog:has(ytd-enforcement-message-view-model),
ytd-popup-container:has(ytd-enforcement-message-view-model),
tp-yt-iron-overlay-backdrop { display: none !important; }

/* Room for our own bottom bar, so the last row of the page is not under it. */
html { --rb-bar-height: 48px; }
ytd-app, #content.ytd-app { padding-bottom: var(--rb-bar-height) !important; }
"""

#: The bar across the bottom — the one thing the phone site had that the
#: desktop site does not. Ours, not Google's: three destinations and no
#: reporting, drawn the way the app draws them, outline for where you are
#: not and solid for where you are.
BAR_JS = """
(function () {
  // Home, Shorts, Subscriptions — the phone app's bar, minus the upload
  // button, because there is nothing here to upload from. Each entry carries
  // both of YouTube's icons for it: the outline for where you are not, the
  // solid one for where you are, which is the whole of how that bar reads.
  const ITEMS = [
    {
      label: 'Home',
      href: 'https://www.youtube.com/',
      at: (p) => p === '/',
      line: 'M12 4.44l7 6.09V20h-4v-6H9v6H5v-9.47l7-6.09m0-1.32L4 10.09V21h6v-6h4v6h6V10.09l-8-6.97z',
      solid: 'M4 21V10.08l8-6.96 8 6.96V21h-7v-6h-2v6H4z',
    },
    {
      label: 'Shorts',
      href: 'https://www.youtube.com/shorts',
      at: (p) => p.startsWith('/shorts'),
      line: 'M10 14.65v-5.3L15 12l-5 2.65zm7.77-4.33c-.77-.32-1.2-.5-1.2-.5L18 9.06c1.84-.96 2.53-3.23 1.56-5.06s-3.24-2.53-5.07-1.56L6.11 6.87c-1.36.72-2.19 2.16-2.12 3.7.07 1.53.99 2.9 2.42 3.48.77.32 1.2.5 1.2.5L6 15.19c-1.84.96-2.53 3.23-1.56 5.06.97 1.83 3.24 2.53 5.07 1.56l8.38-4.43c1.36-.72 2.19-2.16 2.12-3.7-.07-1.53-.99-2.9-2.24-3.36zm-7.16 3.16l4.5-2.38c.68-.36 1.5.14 1.5.88s-.82 1.24-1.5.88l-4.5-2.38z',
      solid: 'M10 14.65v-5.3L15 12l-5 2.65zm7.77-4.33c-.77-.32-1.2-.5-1.2-.5L18 9.06c1.84-.96 2.53-3.23 1.56-5.06s-3.24-2.53-5.07-1.56L6.11 6.87c-1.36.72-2.19 2.16-2.12 3.7.07 1.53.99 2.9 2.42 3.48.77.32 1.2.5 1.2.5L6 15.19c-1.84.96-2.53 3.23-1.56 5.06.97 1.83 3.24 2.53 5.07 1.56l8.38-4.43c1.36-.72 2.19-2.16 2.12-3.7-.07-1.53-.99-2.9-2.24-3.36z',
    },
    {
      label: 'Subscriptions',
      href: 'https://www.youtube.com/feed/subscriptions',
      at: (p) => p.startsWith('/feed/subscriptions'),
      line: 'M18.77 7.63H5.23v-1.5h13.54v1.5zm-1.54-4.5H6.77v1.5h10.46v-1.5zM21 10.13v9c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2v-9c0-1.1.9-2 2-2h14c1.1 0 2 .9 2 2zm-1.5 0c0-.28-.22-.5-.5-.5H5c-.28 0-.5.22-.5.5v9c0 .28.22.5.5.5h14c.28 0 .5-.22.5-.5v-9zM14.5 14.63l-5 3v-6l5 3z',
      solid: 'M18.77 7.63H5.23v-1.5h13.54v1.5zm-1.54-4.5H6.77v1.5h10.46v-1.5zM21 10.13v9c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2v-9c0-1.1.9-2 2-2h14c1.1 0 2 .9 2 2zM14.5 14.63l-5 3v-6l5 3z',
    },
  ];

  const icon = (path) =>
    '<svg viewBox="0 0 24 24" width="24" height="24" focusable="false" ' +
    'style="pointer-events:none;display:block;fill:currentColor">' +
    '<path d="' + path + '"></path></svg>';

  const paint = (bar) => {
    const here = location.pathname;
    for (const link of bar.children) {
      const item = ITEMS[Number(link.dataset.rbIndex)];
      const on = item.at(here);
      link.style.color = on ? '#f1f1f1' : '#aaaaaa';
      link.firstChild.innerHTML = icon(on ? item.solid : item.line);
      link.lastChild.style.fontWeight = on ? '500' : '400';
    }
  };

  const draw = () => {
    // Not on YouTube Music: its own player controls live along the bottom of
    // the window, and a bar of ours on top of them would cover play and skip.
    if (location.hostname !== 'www.youtube.com') { return; }
    if (!document.body) { return; }

    const existing = document.getElementById('rb-bottom-bar');
    if (existing) { paint(existing); return; }

    const bar = document.createElement('nav');
    bar.id = 'rb-bottom-bar';
    bar.style.cssText = [
      'position:fixed', 'left:0', 'right:0', 'bottom:0', 'height:48px',
      'display:flex', 'align-items:stretch', 'z-index:2147483000',
      'background:#0f0f0f', 'border-top:1px solid rgba(255,255,255,0.12)',
      'font-family:Roboto,Arial,sans-serif',
    ].join(';');

    ITEMS.forEach((item, index) => {
      const link = document.createElement('a');
      link.href = item.href;
      link.dataset.rbIndex = String(index);
      link.setAttribute('title', item.label);
      link.style.cssText = [
        'flex:1', 'display:flex', 'flex-direction:column',
        'align-items:center', 'justify-content:center', 'gap:2px',
        'text-decoration:none', 'cursor:pointer', 'padding-top:4px',
      ].join(';');

      const glyph = document.createElement('span');
      glyph.innerHTML = icon(item.line);
      const label = document.createElement('span');
      label.textContent = item.label;
      label.style.cssText = 'font-size:10px;line-height:12px;letter-spacing:0.2px';

      link.appendChild(glyph);
      link.appendChild(label);
      bar.appendChild(link);
    });

    document.body.appendChild(bar);
    paint(bar);
  };

  draw();
  document.addEventListener('DOMContentLoaded', draw);
  // YouTube is a single page app: it swaps the page out without a load, so
  // the bar is redrawn on its own navigation event rather than only on load.
  document.addEventListener('yt-navigate-finish', draw);
})();
"""

#: Opens the left rail, and keeps ads and nags from getting in the way.
#: Only ever presses buttons the page itself provides.
GUIDE_JS = """
(function () {
  // Whether the rail has been opened on your behalf yet, and whether you have
  // since had an opinion of your own about it.
  //
  // Both matter because this used to run on every DOM mutation with no memory
  // at all: closing the rail *is* a mutation, so the hamburger was undone in
  // the same frame you pressed it and the button looked broken. Opening it is
  // a one-off courtesy for a window YouTube thinks is too narrow to pin it —
  // after that the rail is yours.
  let opened = false;
  let yours = false;

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (target && target.closest && target.closest('#guide-button')) {
      yours = true;
    }
  }, true);

  const openGuide = () => {
    if (opened || yours) { return; }
    const app = document.querySelector('ytd-app');
    if (!app) { return; }
    // Already pinned open — nothing to do. YouTube only pins the rail when it
    // thinks the window is wide enough; otherwise it leaves a strip of icons.
    if (app.hasAttribute('guide-persistent-and-visible')) { opened = true; return; }
    if (!document.querySelector('ytd-mini-guide-renderer')) { return; }
    const button = document.querySelector('#guide-button button, #guide-button');
    if (button) { button.click(); opened = true; }
  };

  const tidy = () => {
    // A skippable ad, skipped the moment its button exists.
    for (const cls of ['.ytp-ad-skip-button', '.ytp-ad-skip-button-modern',
                       '.ytp-skip-ad-button', '.ytp-ad-overlay-close-button']) {
      const button = document.querySelector(cls);
      if (button) { button.click(); }
    }
    // A non-skippable one is still just a video playing in the same element.
    const player = document.querySelector('video');
    const showingAd = document.querySelector('.ad-showing, .ytp-ad-player-overlay');
    if (player && showingAd && isFinite(player.duration) && player.duration > 0) {
      player.currentTime = player.duration;
    }
    // The anti-adblock dialog pauses the video behind it. Dismissing it is
    // not enough on its own; the video has to be told to carry on.
    const nag = document.querySelector('ytd-enforcement-message-view-model');
    if (nag) {
      const dialog = nag.closest('tp-yt-paper-dialog');
      if (dialog) { dialog.remove(); }
      nag.remove();
      document.querySelectorAll('tp-yt-iron-overlay-backdrop')
              .forEach((sheet) => sheet.remove());
      if (player && player.paused) { player.play().catch(() => {}); }
    }
  };

  openGuide();
  tidy();
  document.addEventListener('yt-navigate-finish', () => { openGuide(); tidy(); });
  // Ads have to be watched for continuously — they arrive mid-page and
  // mid-video. The rail does not: it is looked at until it has been opened
  // once, and then left alone, because reacting to every mutation is what
  // made the hamburger impossible to use.
  new MutationObserver(() => { openGuide(); tidy(); })
      .observe(document.documentElement, {childList: true, subtree: true});
})();
"""


class ProfileLock:
    """Which running copy of the app owns the persistent web profile.

    A browser engine does not share a profile directory; a second one opening
    it takes turns overwriting it, and the file that loses is the cookie
    store. That is a sign-in disappearing for reasons nothing on screen
    explains, which is exactly how it kept happening.

    A pid in a file is enough to tell the two cases apart. A stale one — from
    a copy that crashed, or was killed — is not an owner, so a lock is never
    something you have to clear by hand.
    """

    def __init__(self, folder: Path) -> None:
        self.path = folder / "owner.pid"
        self.held = False

    def claim(self) -> bool:
        """Take the profile if nothing living has it. Says whether we got it."""
        owner = self.owner()
        if owner is not None and owner != os.getpid():
            return False
        try:
            self.path.write_text(str(os.getpid()))
        except OSError:
            # An unwritable data folder is a problem, but not this one: the
            # profile is still ours to use.
            return True
        self.held = True
        return True

    def owner(self) -> Optional[int]:
        """The pid holding this profile, if one is still running."""
        try:
            pid = int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None                 # it is gone; the lock is stale
        except PermissionError:
            return pid                  # alive, just not ours to signal
        return pid

    def release(self) -> None:
        if not self.held:
            return
        try:
            if self.owner() == os.getpid():
                self.path.unlink()
        except OSError:
            pass
        self.held = False


class AdBlocker(QWebEngineUrlRequestInterceptor):
    """Drops requests for advertising and telemetry before they are made."""

    def __init__(self) -> None:
        super().__init__()
        #: Counted so the interface can say what it stopped, rather than
        #: claiming to block things and never showing evidence.
        self.blocked = 0

    def interceptRequest(self, info) -> None:      # noqa: N802 (Qt's name)
        url = info.requestUrl()
        host = url.host()

        # Match the registrable domain, so `a.b.doubleclick.net` is caught by
        # `doubleclick.net` without a blanket substring test that would also
        # catch `notdoubleclick.net.example.com`.
        parts = host.split(".")
        domains = {".".join(parts[i:]) for i in range(len(parts))}
        if domains & BLOCKED_HOSTS:
            self.blocked += 1
            info.block(True)
            return

        path = url.path()
        if any(path.startswith(prefix) for prefix in BLOCKED_PATHS):
            self.blocked += 1
            info.block(True)


#: How long the cookie store has to go quiet before the page is reloaded, and
#: the longest we wait for that quiet in the first place.
COOKIE_SETTLE_MS = 400
COOKIE_CEILING_MS = 5000

def _as_qt_cookie(cookie):
    """One of our cookies as the QNetworkCookie the web view's store wants.

    Qt takes names and values as bytes rather than text, and an expiry as a
    QDateTime — a session cookie is left with no expiry at all rather than
    given one in the past, which would delete it on the way in.
    """
    from PySide6.QtCore import QByteArray, QDateTime
    from PySide6.QtNetwork import QNetworkCookie

    qt_cookie = QNetworkCookie(QByteArray(cookie.name.encode()),
                               QByteArray(cookie.value.encode()))
    qt_cookie.setDomain(cookie.domain)
    qt_cookie.setPath(cookie.path)
    qt_cookie.setSecure(cookie.secure)
    qt_cookie.setHttpOnly(cookie.http_only)
    if cookie.expires:
        qt_cookie.setExpirationDate(
            QDateTime.fromSecsSinceEpoch(int(cookie.expires)))
    # Google's session cookies are sent from embedded contexts all over its
    # own sites, which is what SameSite=None is for — but Chromium refuses
    # SameSite=None on a cookie that is not also Secure, and refuses it
    # silently, dropping the cookie on the way in. SID, HSID and APISID are
    # exactly that shape: not Secure, and exactly the three cookies that
    # constitute a Google session. Asking for None threw away the sign-in we
    # came here to copy and left the tab looking signed out with no error
    # anywhere. Those get Lax instead — Chromium's own default for a cookie
    # that says nothing, and enough for a top-level navigation to YouTube.
    qt_cookie.setSameSitePolicy(
        QNetworkCookie.SameSite.None_ if cookie.secure
        else QNetworkCookie.SameSite.Lax
    )
    return qt_cookie


class _Page(QWebEnginePage):
    """A page that does not silently drop the windows the site asks for.

    Google's login is opened from a button that calls `window.open`, and a
    QWebEngineView with no `createWindow` throws that request away — so the
    button did nothing at all, with no error and nothing in the log.

    Returning this same page loads the popup in place, which is right for a
    sign-in flow: it is meant to come back to YouTube when it finishes, and a
    second window with no address bar would be a worse place to type a
    password into.
    """

    def createWindow(self, _kind):               # noqa: N802 (Qt's name)
        return self


class YouTubeTab(QWidget):
    """YouTube and YouTube Music, in one tab, without the client that spies."""

    status = Signal(str, str)
    download_requested = Signal(str, str)     # the url on screen, its title

    def __init__(self, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.appearance = appearance
        #: Where the picture goes while fullscreen, and None the rest of the
        #: time. Kept so leaving fullscreen can put the view back.
        self._fullscreen_window: Optional[QWidget] = None
        #: Set when this window had to fall back to a session-only profile,
        #: for whoever built the tab to pass on once they are connected.
        self.shared_session = ""
        self._lock: Optional[ProfileLock] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.profile = self._build_profile()
        self.view = QWebEngineView()
        self.view.setPage(self._build_page())

        layout.addWidget(self._toolbar())
        layout.addWidget(self.view, 1)

        self.view.urlChanged.connect(self._on_url_changed)
        self.view.loadFinished.connect(self._on_load_finished)
        self.go_home()

    def release_profile(self) -> None:
        """Hand the persistent profile back, so the next copy can have it.

        Not required for correctness — a lock held by a dead process is
        ignored anyway — but it means opening the app again straight after
        closing it gets the real profile rather than a session-only one on a
        race with its own predecessor.
        """
        if self._lock is not None:
            self._lock.release()

    # ── Setting up the browser ────────────────────────────────────

    def _build_profile(self) -> QWebEngineProfile:
        """A profile of our own, kept in the app's data folder.

        Named rather than off-the-record, so signing in survives a restart —
        the whole point of allowing a sign-in is that the recommendations are
        yours. Nothing is shared with a system browser in either direction.

        Unless another copy of Rose Bouquet already has it open. Two browser
        engines on one profile do not share it, they take turns overwriting
        it, and what gets overwritten is the cookie store — which is to say
        the sign-in, silently, every time a second window existed. A second
        copy gets a profile that remembers nothing instead, and says so,
        because losing this window's session on close is a great deal better
        than losing the one the first window is still using.
        """
        folder = data_dir() / "youtube"
        folder.mkdir(parents=True, exist_ok=True)

        self._lock = ProfileLock(folder)
        if not self._lock.claim():
            #: Said once the caller has connected to `status`, since a signal
            #: emitted from a constructor is emitted to nobody.
            self.shared_session = (
                "Another copy of Rose Bouquet has the YouTube session open — "
                "this window will not stay signed in. Close the other one and "
                "reopen this tab."
            )
            profile = QWebEngineProfile(self)          # off the record
            profile.setHttpUserAgent(USER_AGENT)
            self._decorate(profile)
            return profile

        profile = QWebEngineProfile("rose-bouquet", self)
        profile.setPersistentStoragePath(str(folder))
        profile.setCachePath(str(folder / "cache"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        profile.setHttpUserAgent(USER_AGENT)
        self._decorate(profile)
        return profile

    def _decorate(self, profile: QWebEngineProfile) -> None:
        """The ad blocker and the injected scripts, which every profile wants.

        Shared because a window that fell back to a session-only profile is
        still a window onto YouTube: it should be as free of ads and as
        familiar as the one that got the real profile.
        """
        self.blocker = AdBlocker()
        profile.setUrlRequestInterceptor(self.blocker)

        # At DocumentCreation there is no documentElement yet to hang a style
        # on, so the stylesheet waits for one rather than throwing into the
        # console on every page load.
        style_js = (
            "(function(){"
            "const add = () => {"
            " if (!document.documentElement || document.getElementById('rb-hide')) return;"
            " const s = document.createElement('style');"
            " s.id = 'rb-hide';"
            f" s.textContent = `{HIDE_CSS}`;"
            " document.documentElement.appendChild(s); };"
            "add();"
            "document.addEventListener('DOMContentLoaded', add);"
            "})();"
        )

        for source, point in (
            (style_js, QWebEngineScript.InjectionPoint.DocumentCreation),
            (GUIDE_JS, QWebEngineScript.InjectionPoint.DocumentReady),
            (BAR_JS, QWebEngineScript.InjectionPoint.DocumentReady),
        ):
            script = QWebEngineScript()
            script.setSourceCode(source)
            script.setInjectionPoint(point)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(True)
            profile.scripts().insert(script)


    def _build_page(self):
        page = _Page(self.profile, self)
        settings = page.settings()
        for attribute, value in (
            (QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False),
            (QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True),
            (QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True),
            # No reason for a video site to know where you are.
            (QWebEngineSettings.WebAttribute.AllowGeolocationOnInsecureOrigins, False),
        ):
            settings.setAttribute(attribute, value)

        # Enabling fullscreen support only lets the page ask. Without this the
        # button in YouTube's player does nothing at all.
        page.fullScreenRequested.connect(self._on_fullscreen_requested)
        return page

    # ── The bar across the top ────────────────────────────────────

    def _toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Sidebar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)

        for text, tip, slot in (
            ("←", "Back", self.view.back),
            ("→", "Forward", self.view.forward),
            ("⟳", "Reload", self.view.reload),
        ):
            button = QPushButton(text)
            button.setObjectName("Quiet")
            button.setToolTip(tip)
            button.setFixedWidth(34)
            button.clicked.connect(slot)
            row.addWidget(button)

        self.watch_button = QPushButton("YouTube")
        self.watch_button.setObjectName("Quiet")
        self.watch_button.clicked.connect(self.go_home)
        row.addWidget(self.watch_button)

        self.music_button = QPushButton("Music")
        self.music_button.setObjectName("Quiet")
        self.music_button.clicked.connect(self.go_music)
        row.addWidget(self.music_button)

        self.address = QLineEdit()
        self.address.setPlaceholderText("Search YouTube, or paste a link…")
        self.address.returnPressed.connect(self._on_address_entered)
        row.addWidget(self.address, 1)

        self.download = QPushButton("Download")
        self.download.setObjectName("Quiet")
        self.download.setToolTip("Download what is on screen")
        self.download.clicked.connect(
            lambda: self.download_requested.emit(
                self.view.url().toString(), self.view.title()))
        row.addWidget(self.download)

        self.sign_in_button = QPushButton("Sign in")
        self.sign_in_button.setObjectName("Quiet")
        self.sign_in_button.setToolTip(
            "Bring your sign-in over from Firefox or Waterfox. Google will not "
            "accept a login typed into an embedded browser, so this copies the "
            "session you already have instead.")
        self.sign_in_button.clicked.connect(self.sign_in)
        row.addWidget(self.sign_in_button)

        self.blocked_label = QLabel("")
        self.blocked_label.setObjectName("Subtle")
        self.blocked_label.setToolTip("Ad and tracking requests stopped this session")
        row.addWidget(self.blocked_label)

        return bar

    # ── Signing in ────────────────────────────────────────────────

    def sign_in(self) -> None:
        """Copy the sign-in out of the browser you already use.

        There is no login form here and there cannot be one: Google refuses to
        authenticate an embedded browser, fingerprinting the engine rather than
        trusting the user agent, so any form of ours ends at "this browser or
        app may not be secure" no matter what it claims to be.

        A sign-in is a handful of cookies, though, and you already have them in
        Waterfox or Firefox. Copying those into this profile signs the tab in —
        in YouTube's own interface, with your feed and your subscriptions —
        without a password being typed into anything of ours. The profile is
        persistent, so this is a one-off rather than something to press again
        every launch.
        """
        from rose_bouquet.core import cookies as jar

        found = jar.read()
        if not found:
            self.status.emit(
                "No Firefox or Waterfox profile found to copy a sign-in from",
                "warning")
            return

        if not jar.signed_in(found):
            self.status.emit(
                "That browser has been to YouTube but is not signed in — "
                "sign in there first, then press this again", "warning")
            return

        store = self.profile.cookieStore()
        for cookie in found:
            store.setCookie(_as_qt_cookie(cookie), QUrl(cookie.url))

        self.status.emit(f"Signed in — copied {len(found)} cookies", "success")
        self._reload_once_cookies_land()

    def _reload_once_cookies_land(self) -> None:
        """Reload when the cookies are actually in, not when we asked.

        `setCookie` is a request, not a write: the store applies it on another
        thread and says so afterwards through `cookieAdded`. Reloading on the
        line after the loop therefore reloaded a page that was still signed
        out, and the sign-in only appeared if you happened to navigate again
        later — which read exactly like it had not worked.

        So the reload waits for the arrivals to stop. Each cookie that lands
        pushes the timer back; the reload happens once none have landed for a
        moment, or after a ceiling, so a cookie Chromium quietly refuses
        cannot leave the page waiting forever.
        """
        store = self.profile.cookieStore()

        settled = QTimer(self)
        settled.setSingleShot(True)
        settled.setInterval(COOKIE_SETTLE_MS)

        def finish() -> None:
            try:
                store.cookieAdded.disconnect(settled.start)
            except (RuntimeError, TypeError):
                pass
            self.view.reload()

        settled.timeout.connect(finish)
        store.cookieAdded.connect(settled.start)
        settled.start()
        QTimer.singleShot(COOKIE_CEILING_MS, lambda: settled.isActive() and finish())

    # ── Going places ──────────────────────────────────────────────

    def go_home(self) -> None:
        self.view.setUrl(QUrl(WATCH_URL))

    def go_music(self) -> None:
        self.view.setUrl(QUrl(MUSIC_URL))

    def _on_address_entered(self) -> None:
        """A link goes there; anything else is a search."""
        text = self.address.text().strip()
        if not text:
            return
        if text.startswith(("http://", "https://")):
            self.view.setUrl(QUrl(text))
            return

        query = QUrl(f"{WATCH_URL}/results")
        parameters = QUrlQuery()
        parameters.addQueryItem("search_query", text)
        query.setQuery(parameters)
        self.view.setUrl(query)

    def _on_url_changed(self, url: QUrl) -> None:
        """Follow the page.

        A `/shorts/<id>` link used to be rewritten to `/watch?v=<id>`, on the
        grounds that it is the same video with a better player. That was only
        defensible while Shorts were being stripped out entirely; now that
        they are a destination on the bar, sending one to a different player
        than the one YouTube opens it in is the app disagreeing with the site
        it is showing.
        """
        self.address.setText(url.toString())

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self.status.emit("That page would not load", "warning")
        if self.blocker.blocked:
            self.blocked_label.setText(f"{self.blocker.blocked} blocked")

    # ── Fullscreen ────────────────────────────────────────────────

    def _on_fullscreen_requested(self, request) -> None:
        """Let the player fill the screen, and give the view back afterwards.

        The whole web view goes fullscreen rather than the video element: the
        page is already drawing its own player chrome, and lifting the element
        out of the document is the sort of thing that ends with a black
        rectangle where the video was.
        """
        request.accept()

        if request.toggleOn():
            if self._fullscreen_window is not None:
                return
            window = QWidget()
            window.setWindowTitle("YouTube")
            frame = QVBoxLayout(window)
            frame.setContentsMargins(0, 0, 0, 0)
            frame.addWidget(self.view)
            window.setWindowState(Qt.WindowState.WindowFullScreen)
            window.show()
            self._fullscreen_window = window
            return

        window = self._fullscreen_window
        if window is None:
            return
        self._fullscreen_window = None
        self.layout().addWidget(self.view)
        self.view.show()
        window.close()
        window.deleteLater()

    def current_url(self) -> str:
        return self.view.url().toString()

    def stage_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance

    def apply_appearance(self, appearance: Appearance) -> None:
        self.stage_appearance(appearance)
