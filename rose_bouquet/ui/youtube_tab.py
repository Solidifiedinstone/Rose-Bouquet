"""YouTube itself, with the hostile parts taken out on the way through.

This replaces a feed built on this machine. That feed was honest — it said why
every item was there — but it only knew what it had been told, so it was always
a worse recommender than the one with a billion hours of watch time behind it.

So: no reimplementation, no scraping, no guessing at an algorithm. This is
YouTube's own mobile site in a web view, with ads, tracking and Shorts removed
before the page ever renders. You can sign in, and if you do you get *your*
recommendations, your subscriptions and your history — the real thing, minus
the client that reports on you.

Three layers do the work, and they are deliberately separate:

* **A request interceptor**, which never lets an ad or telemetry request leave
  the machine. This is the layer that matters: blocking at the network is not
  defeatable by a page that changes its class names next week.
* **Injected CSS and JavaScript**, which hides what did load — Shorts shelves,
  the Shorts tab, promoted rows. Cosmetic, and expected to rot; the
  interceptor is what keeps the promise.
* **A persistent profile**, so a sign-in survives closing the app, kept in
  Rose Bouquet's own data folder rather than anywhere shared.

The one thing this cannot do is lie about what it is. It is a web view onto
Google's site: cookies you accept are real cookies, and signing in means
Google knows you signed in. What it does not do is add anything of its own on
top — no analytics, no identifiers, nothing sent anywhere but YouTube.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import (
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

WATCH_URL = "https://m.youtube.com"
MUSIC_URL = "https://music.youtube.com"

#: Hosts that exist to serve advertising or to report on you. Blocked outright
#: rather than hidden, so nothing is requested and nothing is measured.
#:
#: `googlevideo.com` is deliberately absent — it serves the actual video. Ads
#: come from the hosts below, and from `youtube.com/api/stats/*`, which is
#: handled by path further down.
BLOCKED_HOSTS = frozenset({
    "doubleclick.net",
    "googleadservices.com",
    "googlesyndication.com",
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "adservice.google.com",
    "pagead2.googlesyndication.com",
    "static.doubleclick.net",
    "stats.g.doubleclick.net",
    "ad.doubleclick.net",
})

#: Paths on YouTube's own hosts that only ever carry advertising or telemetry.
BLOCKED_PATHS = (
    "/api/stats/ads",
    "/api/stats/qoe",
    "/api/stats/atr",
    "/pagead/",
    "/ptracking",
    "/generate_204",
)

#: Hidden in the page. Cosmetic and expected to need updating — the request
#: interceptor is what actually keeps ads out.
HIDE_CSS = """
ytm-companion-slot, ytm-promoted-video-renderer, ytm-promoted-sparkles-web-renderer,
.ytp-ad-module, .ytp-ad-overlay-container, ytm-shorts-lockup-view-model,
ytm-reel-shelf-renderer, ytm-rich-section-renderer:has(ytm-shorts-lockup-view-model),
ytd-rich-shelf-renderer[is-shorts], ytd-reel-shelf-renderer,
ytd-promoted-video-renderer, ytd-display-ad-renderer, ytd-ad-slot-renderer,
tp-yt-paper-dialog:has(ytd-consent-bump-v2-lightbox) { display: none !important; }

/* The Shorts tab in the bottom bar. Found by reading the live page rather
   than guessed: it is a class on the tab div, not the tab-identifier
   attribute the desktop site uses. Both the item and its wrapper go, or the
   bar keeps an empty gap where it was. */
ytm-pivot-bar-item-renderer:has(.pivot-bar-item-tab.shorts),
.pivot-bar-item-tab.shorts { display: none !important; }
"""

#: Skips a skippable ad the moment the button appears, and closes overlays.
#: Only ever presses buttons the page itself provides.
SKIP_JS = """
(function () {
  const press = () => {
    for (const cls of ['.ytp-ad-skip-button', '.ytp-ad-skip-button-modern',
                       '.ytp-skip-ad-button', '.ytp-ad-overlay-close-button']) {
      const button = document.querySelector(cls);
      if (button) { button.click(); }
    }
    // A non-skippable ad is still just a video playing in the same element.
    const player = document.querySelector('video');
    const showingAd = document.querySelector('.ad-showing, .ytp-ad-player-overlay');
    if (player && showingAd && isFinite(player.duration) && player.duration > 0) {
      player.currentTime = player.duration;
    }
  };
  press();
  new MutationObserver(press).observe(document.documentElement,
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


class YouTubeTab(QWidget):
    """YouTube and YouTube Music, in one tab, without the client that spies."""

    status = Signal(str, str)
    download_requested = Signal(str)          # the url on screen

    def __init__(self, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.appearance = appearance

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

        # Google refuses to sign you in from something that announces itself as
        # an embedded view — "this browser or app may not be secure" — so the
        # profile presents itself as the browser it actually is: Chromium.
        profile.setHttpUserAgent(
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36")

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
            (SKIP_JS, QWebEngineScript.InjectionPoint.DocumentReady),
        ):
            script = QWebEngineScript()
            script.setSourceCode(source)
            script.setInjectionPoint(point)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(True)
            profile.scripts().insert(script)

        return profile

    def _build_page(self):
        from PySide6.QtWebEngineCore import QWebEnginePage

        page = QWebEnginePage(self.profile, self)
        settings = page.settings()
        for attribute, value in (
            (QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False),
            (QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True),
            (QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True),
            # No reason for a video site to know where you are.
            (QWebEngineSettings.WebAttribute.AllowGeolocationOnInsecureOrigins, False),
        ):
            settings.setAttribute(attribute, value)
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
            lambda: self.download_requested.emit(self.view.url().toString()))
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
        from PySide6.QtCore import QUrlQuery

        parameters = QUrlQuery()
        parameters.addQueryItem("search_query", text)
        query.setQuery(parameters)
        self.view.setUrl(query)

    def _on_url_changed(self, url: QUrl) -> None:
        self.address.setText(url.toString())

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self.status.emit("That page would not load", "warning")
        if self.blocker.blocked:
            self.blocked_label.setText(f"{self.blocker.blocked} blocked")

    def current_url(self) -> str:
        return self.view.url().toString()

    def stage_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance

    def apply_appearance(self, appearance: Appearance) -> None:
        self.stage_appearance(appearance)
