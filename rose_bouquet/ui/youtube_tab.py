"""YouTube itself, with the hostile parts taken out on the way through.

This is not a YouTube client. There is no scraping, no reimplemented feed and
no algorithm of our own: it is YouTube's own desktop site in a web view, with
ads, telemetry and Shorts removed before the page renders. Sign in and you get
*your* recommendations, subscriptions and history — the real thing, minus the
client that reports on you.

The desktop site rather than the phone one, because the desktop site is the one
with the left rail: Home, Subscriptions, You, your playlists, all where they
belong. YouTube hides that rail behind a hamburger whenever it thinks the
window is narrow, so it is opened for you on every page that can hold it. The
phone site's bottom bar is worth keeping too, so a small one is drawn on top —
ours, four buttons, no page of Google's involved.

Four layers do the work, and they are deliberately separate:

* **A request interceptor**, which never lets an ad or a telemetry beacon leave
  the machine. This is the layer that matters: blocking at the network is not
  defeatable by a page that changes its class names next week.
* **Injected CSS**, which hides what did load — Shorts shelves, promoted rows,
  the "turn off your ad blocker" dialog. Cosmetic, and expected to rot; the
  interceptor is what keeps the promise.
* **Injected JavaScript**, which skips an ad that got through, dismisses the
  anti-adblock nag, opens the left rail, sends `/shorts/…` to the ordinary
  player, and draws the bottom bar.
* **A persistent profile**, so a sign-in survives closing the app, kept in Rose
  Bouquet's own data folder rather than anywhere shared.

The one thing this cannot do is lie about what it is. It is a web view onto
Google's site: signing in means Google knows you signed in. What it does not do
is add anything of its own on top — no analytics, no identifiers, nothing sent
anywhere but YouTube, and every beacon YouTube tries to send is dropped here.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QUrlQuery, Signal
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

/* Shorts: the shelves, the rows, the rail entry and the tab. */
ytd-rich-shelf-renderer[is-shorts], ytd-reel-shelf-renderer,
ytm-reel-shelf-renderer, ytm-shorts-lockup-view-model,
ytd-rich-section-renderer:has(ytd-rich-shelf-renderer[is-shorts]),
ytd-guide-entry-renderer:has(a[title="Shorts"]),
ytd-mini-guide-entry-renderer[aria-label="Shorts"],
ytm-pivot-bar-item-renderer:has(.pivot-bar-item-tab.shorts),
.pivot-bar-item-tab.shorts { display: none !important; }

/* Room for our own bottom bar, so the last row of the page is not under it. */
html { --rb-bar-height: 46px; }
ytd-app, #content.ytd-app { padding-bottom: var(--rb-bar-height) !important; }
"""

#: The bar across the bottom — the one thing the phone site had that the
#: desktop site does not. Ours, not Google's: four links and no reporting.
BAR_JS = """
(function () {
  const ITEMS = [
    ['Home', 'https://www.youtube.com/'],
    ['Subscriptions', 'https://www.youtube.com/feed/subscriptions'],
    ['You', 'https://www.youtube.com/feed/you'],
    ['Music', 'https://music.youtube.com/'],
  ];

  const draw = () => {
    if (document.getElementById('rb-bottom-bar') || !document.body) { return; }
    // Not on YouTube Music: its own player controls live along the bottom of
    // the window, and a bar of ours on top of them would cover play and skip.
    if (location.hostname !== 'www.youtube.com') { return; }

    const bar = document.createElement('nav');
    bar.id = 'rb-bottom-bar';
    bar.style.cssText = [
      'position:fixed', 'left:0', 'right:0', 'bottom:0', 'height:46px',
      'display:flex', 'align-items:stretch', 'justify-content:space-around',
      'z-index:2147483000', 'background:#0f0f0f',
      'border-top:1px solid rgba(255,255,255,0.12)',
      'font-family:Roboto,Arial,sans-serif', 'font-size:12px',
    ].join(';');

    for (const [label, href] of ITEMS) {
      const link = document.createElement('a');
      link.textContent = label;
      link.href = href;
      link.style.cssText = [
        'flex:1', 'display:flex', 'align-items:center', 'justify-content:center',
        'color:#f1f1f1', 'text-decoration:none', 'cursor:pointer',
      ].join(';');
      // Current page gets the accent, so the bar says where you are.
      if (location.href.replace(/\\/$/, '') === href.replace(/\\/$/, '')) {
        link.style.color = '#ff6a8a';
      }
      bar.appendChild(link);
    }

    document.body.appendChild(bar);
  };

  draw();
  document.addEventListener('DOMContentLoaded', draw);
  // YouTube is a single page app: it swaps the page out without a load, so
  // the bar is redrawn on its own navigation event rather than only on load.
  document.addEventListener('yt-navigate-finish', () => { draw(); });
})();
"""

#: Opens the left rail, and keeps ads and nags from getting in the way.
#: Only ever presses buttons the page itself provides.
GUIDE_JS = """
(function () {
  const openGuide = () => {
    const app = document.querySelector('ytd-app');
    if (!app) { return; }
    // Already pinned open — nothing to do. YouTube only pins the rail when it
    // thinks the window is wide enough; otherwise it leaves a strip of icons.
    if (app.hasAttribute('guide-persistent-and-visible')) { return; }
    if (!document.querySelector('ytd-mini-guide-renderer')) { return; }
    const button = document.querySelector('#guide-button button, #guide-button');
    if (button) { button.click(); }
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

  const tick = () => { openGuide(); tidy(); };
  tick();
  document.addEventListener('yt-navigate-finish', tick);
  new MutationObserver(tick).observe(document.documentElement,
                                     {childList: true, subtree: true});
})();
"""


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


#: Google's login. A dead end from in here, and the reason for the detour
#: below — see `_Page.acceptNavigationRequest`.
SIGN_IN_HOST = "accounts.google.com"


class _Page(QWebEnginePage):
    """A page that opens the site's own windows, and refuses one of them."""

    #: Someone tried to sign in from the web view. Carried out to the window
    #: rather than handled here, because the flow that works is not in this
    #: widget at all.
    sign_in_requested = Signal()

    def createWindow(self, _kind):               # noqa: N802 (Qt's name)
        """Load a popup in place instead of dropping it.

        YouTube opens several things with `window.open`, and a page with no
        `createWindow` throws every one of them away silently — no error, no
        log line, the button simply does nothing.
        """
        return self

    def acceptNavigationRequest(self, url, kind, is_main_frame):  # noqa: N802
        """Turn a walk into Google's login around at the door.

        Google will not authenticate an embedded browser. It does not matter
        what the user agent claims — it fingerprints the engine, and answers
        "Couldn't sign you in / This browser or app may not be secure". There
        is no header to set and no flag to pass that changes this; it is the
        policy, and it applies to every embedded view including this one.

        So the sign-in used to end at a wall with a Try again button that
        could only ever fail again. Rather than let that happen, a main-frame
        navigation to Google's login is stopped here and the app offers the
        device-code flow instead, which Google *does* accept — because it is
        the one it designed for exactly this situation.

        Only the main frame, and only Google's login host: the page's own
        background calls to that domain are how a session that already exists
        stays alive, and blocking those would sign you out rather than in.
        """
        if is_main_frame and url.host() == SIGN_IN_HOST:
            self.sign_in_requested.emit()
            return False
        return super().acceptNavigationRequest(url, kind, is_main_frame)


class YouTubeTab(QWidget):
    """YouTube and YouTube Music, in one tab, without the client that spies."""

    status = Signal(str, str)
    download_requested = Signal(str, str)     # the url on screen, its title
    #: Someone pressed Sign in here, where it cannot work. The window sends
    #: them to the native tab's device-code flow, which can.
    sign_in_requested = Signal()

    def __init__(self, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.appearance = appearance
        #: Where the picture goes while fullscreen, and None the rest of the
        #: time. Kept so leaving fullscreen can put the view back.
        self._fullscreen_window: Optional[QWidget] = None

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

    # ── Setting up the browser ────────────────────────────────────

    def _build_profile(self) -> QWebEngineProfile:
        """A profile of our own, kept in the app's data folder.

        Named rather than off-the-record, so signing in survives a restart —
        the whole point of allowing a sign-in is that the recommendations are
        yours. Nothing is shared with a system browser in either direction.
        """
        folder = data_dir() / "youtube"
        folder.mkdir(parents=True, exist_ok=True)

        profile = QWebEngineProfile("rose-bouquet", self)
        profile.setPersistentStoragePath(str(folder))
        profile.setCachePath(str(folder / "cache"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        profile.setHttpUserAgent(USER_AGENT)

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

        return profile

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
        page.sign_in_requested.connect(self.sign_in_requested)
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

        self.blocked_label = QLabel("")
        self.blocked_label.setObjectName("Subtle")
        self.blocked_label.setToolTip("Ad and tracking requests stopped this session")
        row.addWidget(self.blocked_label)

        return bar

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
        """Follow the page, and send a Short to the ordinary player.

        `/shorts/<id>` is the same video as `/watch?v=<id>`, played in a
        full-screen vertical feed with no controls worth the name. Since the
        Shorts shelves are hidden anyway, a link that lands on one — from a
        search result, or from outside the app — is rewritten rather than
        followed.
        """
        text = url.toString()
        path = url.path()
        if path.startswith("/shorts/"):
            video_id = path[len("/shorts/"):].split("/")[0]
            if video_id:
                self.view.setUrl(QUrl(f"{WATCH_URL}/watch?v={video_id}"))
                return

        self.address.setText(text)

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
