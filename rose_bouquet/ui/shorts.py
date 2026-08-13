"""Shorts — the vertical ones, in a shape that suits them.

A short is a different thing from a video and wants a different screen. They
are portrait, they are under a minute, and they are watched one after another
rather than chosen from a list, so this is a column of tall thumbnails rather
than the wide rows the Watch tab uses, and playing one queues up the rest.

The stage is this view's own rather than the Watch tab's. Two reasons: a
portrait video in a landscape frame is mostly empty space, so this one is
shaped for it; and moving between Shorts and Watch should not interrupt what
is playing on the other.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core.recommend import Candidate
from rose_bouquet.ui.theme import Appearance
from rose_bouquet.ui.thumbnails import Thumbnail, youtube_thumbnail
from rose_bouquet.ui.views import ScrollingView
from rose_bouquet.ui.widgets import SectionHeading

logger = logging.getLogger(__name__)

#: How far a wheel has to travel before it counts as one flick. A mouse
#: notch is 120; a trackpad sends many smaller deltas for a single gesture.
WHEEL_STEP = 120

#: Portrait, because that is the shape of the thing.
CARD_WIDTH = 132
CARD_HEIGHT = 234
COLUMNS = 5

#: How many cards the wall draws. The reel keeps every short it has fetched —
#: they are small — but each card is a widget with a picture to fetch, and a
#: few hundred of those is a slow, memory-hungry screen for a wall nobody
#: scrolls to the bottom of.
WALL_LIMIT = 60


class ShortsView(ScrollingView):
    """A wall of vertical videos."""

    search_requested = Signal(str)
    refresh_requested = Signal()
    watch_requested = Signal(object)      # Candidate
    download_requested = Signal(object)
    like_toggled = Signal(object)
    status = Signal(str, str)
    #: Running low on reel. Ask for more before the bottom is reached.
    more_requested = Signal()

    def __init__(self, tastes, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        self.tastes = tastes
        self.shorts: list[Candidate] = []
        self.note = ""
        self.loading = False
        self.progress_text = ""
        self.video = None

        #: Reel mode: one short filling the view, scrolled through like the
        #: app everybody already knows.
        self.reel = False
        self.reel_at = 0
        self._wheel = 0
        #: Set while a further batch is on its way, so passing the halfway
        #: mark repeatedly does not ask for the same batch five times.
        self.fetching_more = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        title = QLabel("Shorts")
        title.setObjectName("Heading")
        self.header_layout.addWidget(title)
        self.header_layout.addStretch(1)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Shorts…")
        self.search.setFixedWidth(240)
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._search)
        self.header_layout.addWidget(self.search)

        refresh = QPushButton("Refresh")
        refresh.setObjectName("Quiet")
        refresh.setToolTip("Fetch shorts from the channels you follow")
        refresh.clicked.connect(self.refresh_requested.emit)
        self.header_layout.addWidget(refresh)

    def attach_video(self, stage) -> None:
        """Give this view its own video surface.

        In reel mode it takes the whole view; otherwise it is hidden and the
        wall has the space.
        """
        self.video = stage
        self.outer.insertWidget(1, stage, 1)
        stage.closed.connect(self.leave_reel)
        stage.finished.connect(self._rolled_on)

    # ── The reel ──────────────────────────────────────────────────

    def play_at(self, index: int) -> None:
        """Enter the reel at this position and start playing."""
        if not 0 <= index < len(self.shorts):
            return

        self.reel = True
        self.reel_at = index
        self.scroll.setVisible(False)
        self.header.setVisible(False)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.watch_requested.emit(self.shorts[index])

        # Ask for more once past the halfway mark, so the next batch is on its
        # way long before it is needed. Waiting for the last one means a stall
        # at exactly the moment somebody is in the rhythm of scrolling.
        if index >= len(self.shorts) // 2 and not self.fetching_more:
            self.fetching_more = True
            self.more_requested.emit()

    def step(self, direction: int) -> None:
        """Move one short along the reel. Stops at the ends rather than wrapping.

        Wrapping a short list of eight would put you back at the top after a
        few flicks, which reads as the thing being stuck.
        """
        if not self.reel:
            return
        target = self.reel_at + direction
        if 0 <= target < len(self.shorts):
            self.play_at(target)

    def leave_reel(self) -> None:
        self.reel = False
        self.scroll.setVisible(True)
        self.header.setVisible(True)
        if self.video is not None:
            self.video.setVisible(False)
        self.refresh()

    def _rolled_on(self) -> None:
        """One finished. Roll on, the way the format is meant to work."""
        if not self.reel:
            return
        if self.reel_at + 1 < len(self.shorts):
            self.step(1)
        elif self.fetching_more:
            # More is coming; hold on this one rather than stopping dead.
            self.play_at(self.reel_at)
        else:
            # The end of what has been fetched: replay rather than stopping
            # dead on a black rectangle.
            self.play_at(self.reel_at)

    # ── Gestures ──────────────────────────────────────────────────
    #
    # Deliberately thin, and all in one place: a touch port has to replace the
    # wheel with a swipe and nothing else about the reel should have to change.

    def wheelEvent(self, event) -> None:
        if not self.reel:
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        # A trackpad sends a stream of small deltas for one flick, so they are
        # accumulated and spent — otherwise one gesture skips five shorts.
        self._wheel += delta
        if abs(self._wheel) >= WHEEL_STEP:
            self.step(-1 if self._wheel > 0 else 1)
            self._wheel = 0
        event.accept()

    def keyPressEvent(self, event) -> None:
        if self.reel:
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_J):
                self.step(1)
                return
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_K):
                self.step(-1)
                return
            if event.key() == Qt.Key.Key_Escape:
                self.leave_reel()
                return
        super().keyPressEvent(event)

    # ── Contents ──────────────────────────────────────────────────

    def _search(self) -> None:
        query = self.search.text().strip()
        if query:
            self.search_requested.emit(query)

    def show_progress(self, text: str) -> None:
        self.loading = True
        self.progress_text = text
        self.refresh()

    def show_shorts(self, shorts: list, note: str = "") -> None:
        self.loading = False
        self.fetching_more = False
        self.shorts = list(shorts)
        self.note = note
        self.refresh()

    def add_shorts(self, more: list) -> None:
        """Extend the reel, skipping anything already in it."""
        self.fetching_more = False
        seen = {c.id for c in self.shorts}
        fresh = [c for c in more if c.id and c.id not in seen]
        if not fresh:
            return

        self.shorts.extend(fresh)
        if not self.reel:
            self.refresh()

    # ── Drawing ───────────────────────────────────────────────────

    def refresh(self, *_args) -> None:
        # Nothing to draw while the reel is covering the wall, and this is
        # called on every track change — which in a reel is every scroll. It
        # was rebuilding sixty hidden cards per flick.
        if self.reel:
            return

        self.clear(self.body_layout)

        if self.loading:
            self.body_layout.addWidget(
                self.empty_label(self.progress_text or "Looking for shorts…"))
            self.body_layout.addStretch(1)
            return

        if not self.shorts:
            self.body_layout.addWidget(self.empty_label(
                "No shorts yet.\n\nSearch above, or press Refresh to pull them "
                "from the channels you follow."
            ))
            self.body_layout.addStretch(1)
            return

        self.body_layout.addWidget(SectionHeading(
            self.note or "Shorts", self.appearance, count=len(self.shorts)))

        wall = QWidget()
        grid = QGridLayout(wall)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setSpacing(10)

        shown = self.shorts[:WALL_LIMIT]
        for index, item in enumerate(shown):
            grid.addWidget(self._card(item, index), index // COLUMNS, index % COLUMNS)

        # A trailing stretch column, so a half-full last row stays left-aligned
        # instead of spreading its cards across the width.
        grid.setColumnStretch(COLUMNS, 1)
        self.body_layout.addWidget(wall)

        if len(self.shorts) > WALL_LIMIT:
            self.body_layout.addWidget(self.empty_label(
                f"…and {len(self.shorts) - WALL_LIMIT} more in the reel."))

        self.body_layout.addStretch(1)

    def _card(self, item: Candidate, index: int = 0) -> QWidget:

        card = QWidget()
        card.setObjectName("TrackRow")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setFixedWidth(CARD_WIDTH)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(4, 4, 4, 6)
        layout.setSpacing(4)

        picture = Thumbnail(item.thumbnail or youtube_thumbnail(item.id),
                            CARD_WIDTH - 8, CARD_HEIGHT, self.appearance, glyph="▶")
        layout.addWidget(picture)

        title = QLabel(item.title)
        title.setObjectName("RowTitle")
        title.setWordWrap(True)
        title.setMaximumHeight(34)
        layout.addWidget(title)

        artist = QLabel(item.artist)
        artist.setObjectName("Subtle")
        layout.addWidget(artist)

        row = QHBoxLayout()
        row.setSpacing(2)

        liked = self.tastes.likes(item.id)
        like = QPushButton("♥" if liked else "♡")
        like.setObjectName("Quiet")
        like.setProperty("liked", "true" if liked else "false")
        like.setToolTip("Like")
        like.clicked.connect(lambda: self.like_toggled.emit(item))
        row.addWidget(like)

        download = QPushButton("↓")
        download.setObjectName("Quiet")
        download.setToolTip("Download the audio")
        download.clicked.connect(lambda: self.download_requested.emit(item))
        row.addWidget(download)
        row.addStretch(1)
        layout.addLayout(row)

        # The whole card is the play button — picking a short out of a wall by
        # hitting a small triangle is not how anybody watches these.
        # Opens the reel at this card rather than playing it in place: the
        # point of a wall of shorts is to fall into it.
        card.mousePressEvent = lambda _event, i=index: self.play_at(i)
        return card
