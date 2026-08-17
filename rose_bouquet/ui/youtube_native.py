"""YouTube as an actual app: widgets, not a browser.

This is the tab the embedded Chromium used to be. Everything on screen is a Qt
widget drawing data from `core/innertube.py`, and the video plays in the app's
own player, so there is no browser engine in the process and no page of
Google's HTML anywhere near it.

What that buys, in order of how much it matters:

* **No ads at all.** Not blocked, not skipped — absent. An ad break is
  something a *player* is told to insert, and this player is ours.
* **No telemetry.** The only requests made are the ones something on screen
  needs an answer to. There is no beacon to block because nothing here wants
  to send one.
* **Signing in works.** The device-code flow shows you a short code to type
  into google.com/device, which is what a television does, and Google's
  "this browser or app may not be secure" refusal never comes up.
* **A few hundred megabytes of RAM back.**

What it costs, honestly: this draws the parts of YouTube the app uses, and
that is not all of YouTube. Comments, live chat, channel pages and the rest
are not here yet. The web view is still one button away for those, until it
is not needed at all.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core import innertube
from rose_bouquet.core.media import Candidate
from rose_bouquet.ui import tasks
from rose_bouquet.ui.theme import Appearance
from rose_bouquet.ui.thumbnails import Thumbnail, youtube_thumbnail
from rose_bouquet.ui.views import ScrollingView

logger = logging.getLogger(__name__)

#: How wide one card is, picture included. The grid fits as many as it can.
CARD_WIDTH = 260
THUMB_HEIGHT = 146


def _clock(seconds: int) -> str:
    if seconds <= 0:
        return ""
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


class SignIn(QWidget):
    """The device-code flow, as a panel rather than a login form.

    There is no password box here on purpose, and there could not be one: the
    app never sees your Google password. It asks Google for a short code, you
    type that code into google.com/device on whatever device already has you
    signed in, and Google hands the app a token for the account. The same
    thing a smart TV does, for the same reason — no browser to log in with.
    """

    signed_in = Signal()
    status = Signal(str, str)

    #: Shown before a code is asked for, and again whenever the panel is put
    #: back to its resting state.
    INTRO = (
        "YouTube has no home feed for someone who is not signed in — "
        "subscriptions and recommendations are the account's, not the app's.\n\n"
        "Signing in here never sees your password: the app gets a code, you "
        "type it in on a device you are already signed in on."
    )

    def __init__(self, auth: innertube.Auth, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.appearance = appearance
        self.code: Optional[innertube.DeviceCode] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 30, 20, 20)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.heading = QLabel("Sign in for your own feed")
        self.heading.setObjectName("Heading")
        self.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.heading)

        self.explain = QLabel(self.INTRO)
        self.explain.setObjectName("Subtle")
        self.explain.setWordWrap(True)
        self.explain.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.explain.setMaximumWidth(520)
        layout.addWidget(self.explain, 0, Qt.AlignmentFlag.AlignHCenter)

        #: The code, once there is one. Big, because it is meant to be read
        #: off the screen and typed into a phone.
        self.code_label = QLabel("")
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code_label.setVisible(False)
        layout.addWidget(self.code_label)

        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.button = QPushButton("Sign in")
        self.button.setObjectName("Primary")
        self.button.clicked.connect(self.begin)
        row.addWidget(self.button)

        self.copy = QPushButton("Copy the code")
        self.copy.setObjectName("Quiet")
        self.copy.setVisible(False)
        self.copy.clicked.connect(self._copy)
        row.addWidget(self.copy)
        layout.addLayout(row)

        #: Polls Google to ask whether the code has been entered yet.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)

        self.apply_appearance(appearance)

    # ── The flow ──────────────────────────────────────────────────

    def reset(self) -> None:
        """Put the panel back to how it looked before anyone pressed anything.

        Called every time the panel is opened. Without it, signing out and
        signing back in during one run showed a disabled button still reading
        "Waiting for you…" beside the *previous* code, with no way forward
        short of restarting the app — the panel remembered a sign-in that had
        already happened and been undone.
        """
        self.timer.stop()
        self.code = None
        self.code_label.setVisible(False)
        self.code_label.clear()
        self.copy.setVisible(False)
        self.heading.setText("Sign in for your own feed")
        self.explain.setText(self.INTRO)
        self.button.setEnabled(True)
        self.button.setText("Sign in")

    def begin(self) -> None:
        self.button.setEnabled(False)
        self.button.setText("Asking Google for a code…")

        tasks.run(self.auth.start, on_done=self._got_code, on_error=self._failed)

    def _got_code(self, code: innertube.DeviceCode) -> None:
        self.code = code
        self.code_label.setText(code.user_code)
        self.code_label.setVisible(True)
        self.copy.setVisible(True)

        self.heading.setText("Type this code in")
        self.explain.setText(
            f"Go to {code.verification_url} on your phone or in a browser, and "
            f"enter the code above.\n\nThis window will notice by itself when "
            f"you have. The code is good for "
            f"{max(1, code.expires_in // 60)} minutes."
        )
        self.button.setText("Waiting for you…")

        # Google rejects polling faster than the interval it asked for, so its
        # number is used rather than one of ours.
        self.timer.start(max(2, code.interval) * 1000)

    def _poll(self) -> None:
        if self.code is None:
            self.timer.stop()
            return
        tasks.run(self.auth.poll, self.code, on_done=self._polled,
                  on_error=self._failed)

    def _polled(self, done: bool) -> None:
        if not done:
            # `poll` widens the interval when Google says we are asking too
            # often, so the timer follows it rather than keeping its old pace.
            if self.code is not None:
                wanted = max(2, self.code.interval) * 1000
                if self.timer.interval() != wanted:
                    self.timer.start(wanted)
            return                          # still waiting; the timer comes back
        self.timer.stop()
        self.status.emit("Signed in to YouTube", "success")
        self.signed_in.emit()

    def _failed(self, message: str) -> None:
        self.reset()
        self.button.setText("Try again")
        self.status.emit(str(message), "error")

    def _copy(self) -> None:
        if self.code is not None:
            QApplication.clipboard().setText(self.code.user_code)
            self.status.emit("Code copied", "info")

    # ── Looks ─────────────────────────────────────────────────────

    def stage_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance

    def apply_appearance(self, appearance: Appearance) -> None:
        self.stage_appearance(appearance)
        self.code_label.setStyleSheet(
            f"font-size: 30px; font-weight: 600; letter-spacing: 4px;"
            f" color: {appearance.theme.accent};"
        )


class YouTubeNativeView(ScrollingView):
    """YouTube's feeds, drawn as widgets."""

    play_requested = Signal(object)          # Candidate
    #: The whole Candidate, not a url and a title. The web view can only report
    #: what its tab is called, but this tab already knows the channel, so the
    #: downloaded file can be tagged with it instead of guessing from a title.
    download_requested = Signal(object)      # Candidate
    web_view_requested = Signal()
    status = Signal(str, str)

    #: The buttons across the top, and the feed each one asks for. All three
    #: need an account, because YouTube has no anonymous feeds left.
    FEEDS = (
        ("Home", innertube.HOME),
        ("Subscriptions", innertube.SUBSCRIPTIONS),
        ("History", innertube.HISTORY),
    )

    def __init__(self, appearance: Appearance, tube: Optional[innertube.InnerTube] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        self.tube = tube or innertube.InnerTube()

        self.items: list[Candidate] = []
        self.token = ""
        self.note = ""
        self.loading = False
        #: What is on screen: a feed id, or ("search", query).
        self.where: tuple[str, str] = ("feed", innertube.HOME)
        #: Columns last drawn, so a resize only rebuilds when it has to.
        self._columns = 0

        self._build_header()

        self.sign_in = SignIn(self.tube.auth, appearance)
        self.sign_in.signed_in.connect(self._after_sign_in)
        self.sign_in.status.connect(self.status)
        self.sign_in.setVisible(False)
        self.outer.insertWidget(1, self.sign_in)

    def _build_header(self) -> None:
        title = QLabel("YouTube")
        title.setObjectName("Heading")
        self.header_layout.addWidget(title)

        self.feed_buttons: dict[str, QPushButton] = {}
        for label, browse_id in self.FEEDS:
            button = QPushButton(label)
            button.setObjectName("Quiet")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, where=browse_id: self.open_feed(where))
            self.header_layout.addWidget(button)
            self.feed_buttons[browse_id] = button

        self.header_layout.addStretch(1)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search YouTube…")
        self.search_box.setFixedWidth(240)
        self.search_box.setClearButtonEnabled(True)
        self.search_box.returnPressed.connect(
            lambda: self.open_search(self.search_box.text()))
        self.header_layout.addWidget(self.search_box)

        self.account = QPushButton("Sign in")
        self.account.setObjectName("Quiet")
        self.account.clicked.connect(self._account_pressed)
        self.header_layout.addWidget(self.account)

        web = QPushButton("Web view")
        web.setObjectName("Quiet")
        web.setToolTip("The old embedded browser — comments, channel pages, "
                       "anything this tab cannot draw yet")
        web.clicked.connect(self.web_view_requested.emit)
        self.header_layout.addWidget(web)

    # ── Asking for things ─────────────────────────────────────────

    def open_feed(self, browse_id: str = innertube.HOME) -> None:
        self.where = ("feed", browse_id)
        self.token = ""
        self.items = []
        self._load()

    def open_search(self, query: str) -> None:
        query = query.strip()
        if not query:
            return
        self.where = ("search", query)
        self.token = ""
        self.items = []
        self._load()

    def load_more(self) -> None:
        if self.token:
            self._load(more=True)

    def _load(self, *, more: bool = False) -> None:
        # Asking for anything means leaving the sign-in panel — searching is
        # the one thing that works signed out, and it has to have somewhere to
        # put its results.
        self.sign_in.setVisible(False)
        self.scroll.setVisible(True)

        kind, what = self.where
        token = self.token if more else ""
        self.loading = True
        self.refresh()

        def work() -> innertube.Page:
            if kind == "search":
                return self.tube.search(what, token=token)
            return self.tube.feed(what, token=token)

        def done(page: innertube.Page) -> None:
            self.loading = False
            self.token = page.token
            self.note = page.note
            # Appended rather than replaced, so "more" grows the grid instead
            # of throwing away what you were looking at.
            known = {item.id for item in self.items}
            self.items.extend(item for item in page.items if item.id not in known)
            self._sync_account_button()
            self.refresh()

        def failed(message: str) -> None:
            self.loading = False
            self.note = str(message)
            self._sync_account_button()
            self.refresh()
            self.status.emit(f"YouTube: {message}", "error")

        tasks.run(work, on_done=done, on_error=failed)

    # ── Signing in and out ────────────────────────────────────────

    def _account_pressed(self) -> None:
        if self.tube.signed_in:
            self.tube.auth.sign_out()
            self.items = []
            self.token = ""
            self._sync_account_button()
            self.status.emit("Signed out of YouTube", "info")
            self.refresh()
            return

        self.open_sign_in()

    def open_sign_in(self) -> None:
        """Show the sign-in panel, always from a clean slate.

        Public because the web view sends people here: pressing Sign in over
        there cannot work, so it hands over rather than showing Google's
        refusal.
        """
        self.sign_in.reset()
        self.sign_in.setVisible(True)
        self.scroll.setVisible(False)

    def _after_sign_in(self) -> None:
        self.sign_in.setVisible(False)
        self.scroll.setVisible(True)
        self._sync_account_button()
        self.open_feed(innertube.HOME)

    def _sync_account_button(self) -> None:
        self.account.setText("Sign out" if self.tube.signed_in else "Sign in")

    # ── Drawing ───────────────────────────────────────────────────

    def columns(self) -> int:
        return max(1, (self.width() - 40) // CARD_WIDTH)

    def resizeEvent(self, event) -> None:     # noqa: N802 (Qt's name)
        super().resizeEvent(event)
        # Only when the number of cards per row actually changes; otherwise
        # every pixel of a window drag rebuilds the whole grid.
        if self.columns() != self._columns and self.items:
            self.refresh()

    def refresh(self, *_args) -> None:
        self.clear(self.body_layout)

        for browse_id, button in self.feed_buttons.items():
            button.setChecked(self.where == ("feed", browse_id))

        if self.items:
            self.body_layout.addWidget(self._grid())
            if self.token:
                more = QPushButton("Show more")
                more.setObjectName("Quiet")
                more.clicked.connect(self.load_more)
                self.body_layout.addWidget(more, 0, Qt.AlignmentFlag.AlignHCenter)

        if self.loading:
            self.body_layout.addWidget(self.empty_label("Asking YouTube…"))
        elif not self.items:
            self.body_layout.addWidget(self.empty_label(
                self.note or "Nothing here. Try a search."))

        self.body_layout.addStretch(1)

    def _grid(self) -> QWidget:
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(4, 8, 4, 8)
        grid.setSpacing(14)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._columns = self.columns()
        for index, item in enumerate(self.items):
            grid.addWidget(self._card(item), index // self._columns,
                           index % self._columns)
        return holder

    def _card(self, item: Candidate) -> QWidget:
        card = QWidget()
        card.setObjectName("TrackRow")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setFixedWidth(CARD_WIDTH - 14)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        # The whole card plays it. A play button on a thumbnail is a smaller
        # target for the same intent.
        card.mousePressEvent = lambda _event, item=item: self.play_requested.emit(item)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 10)
        layout.setSpacing(6)

        picture = QWidget()
        stack = QHBoxLayout(picture)
        stack.setContentsMargins(0, 0, 0, 0)
        thumbnail = Thumbnail(
            item.thumbnail or youtube_thumbnail(item.id, big=True),
            CARD_WIDTH - 30, THUMB_HEIGHT, self.appearance)
        stack.addWidget(thumbnail)
        layout.addWidget(picture)

        title = QLabel(item.title)
        title.setObjectName("RowTitle")
        title.setWordWrap(True)
        title.setMaximumHeight(42)
        layout.addWidget(title)

        facts = " • ".join(part for part in (
            item.artist, item.published, _clock(item.duration)) if part)
        subtitle = QLabel(facts)
        subtitle.setObjectName("Subtle")
        layout.addWidget(subtitle)

        row = QHBoxLayout()
        row.setSpacing(4)
        row.addStretch(1)
        download = QPushButton("↓")
        download.setObjectName("Quiet")
        download.setToolTip("Download the audio")
        download.clicked.connect(lambda: self.download_requested.emit(item))
        row.addWidget(download)
        layout.addLayout(row)

        return card

    def stage_appearance(self, appearance: Appearance) -> None:
        super().stage_appearance(appearance)
        self.sign_in.stage_appearance(appearance)

    def apply_appearance(self, appearance: Appearance) -> None:
        self.stage_appearance(appearance)
        self.sign_in.apply_appearance(appearance)
        self.refresh()

    def first_load(self) -> None:
        """Fill the tab the first time it is shown, not on app startup.

        A network round trip during startup is a window that takes a second
        longer to appear, for a tab the user may not even open.
        """
        if self.items or self.loading:
            return
        if self.tube.signed_in:
            self.open_feed(innertube.HOME)
            return

        # Signed out there is no feed to fetch — YouTube does not have one —
        # so the tab opens on the thing that would fix that.
        self.open_sign_in()
