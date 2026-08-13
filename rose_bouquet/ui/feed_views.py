"""For You, and Subscriptions — the parts driven by the local profile."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core.recommend import Scored, top_artists
from rose_bouquet.core.tastes import Channel, Tastes
from rose_bouquet.ui.theme import Appearance
from rose_bouquet.ui.thumbnails import Thumbnail, youtube_thumbnail
from rose_bouquet.ui.views import ScrollingView
from rose_bouquet.ui.widgets import SectionHeading


class FeedView(ScrollingView):
    """A feed built here, from what you follow and what you have played.

    Nothing about this ranking happens on a server. The candidates come from
    subscriptions and from related-track lookups; the ordering is
    `core.recommend`, running on this machine against a file you own.
    """

    play_requested = Signal(object)       # Candidate
    download_requested = Signal(object)   # Candidate
    like_toggled = Signal(object)         # Candidate
    refresh_requested = Signal()
    search_requested = Signal(str)
    watch_requested = Signal(object)      # Candidate
    status = Signal(str, str)

    def __init__(self, tastes: Tastes, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        self.tastes = tastes
        self.ranked: list[Scored] = []
        self.loading = False
        self.progress_text = ""

        #: Search results or a channel's uploads, shown in place of the feed.
        #: Same rows, same actions — the only difference is where the list came
        #: from, so there is no second screen to keep in step with this one.
        self.results: list[Scored] = []
        self.results_note = ""
        #: Set by `attach_video`. None means this view cannot show video, which
        #: is a normal state rather than an error.
        self.video = None

        title = QLabel("Watch")
        title.setObjectName("Heading")
        self.header_layout.addWidget(title)
        self.header_layout.addStretch(1)

        self.summary = QLabel()
        self.summary.setObjectName("Subtle")
        self.header_layout.addWidget(self.summary)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search YouTube…")
        self.search.setFixedWidth(260)
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._search)
        # Clearing the box is the way back to the feed, so it has to be the
        # same control rather than a separate "back" the user has to find.
        self.search.textChanged.connect(self._maybe_restore)
        self.header_layout.addWidget(self.search)

        rebuild = QPushButton("Rebuild")
        rebuild.setObjectName("Quiet")
        rebuild.setToolTip("Check your subscriptions and re-rank")
        rebuild.clicked.connect(self.refresh_requested.emit)
        self.header_layout.addWidget(rebuild)

    def attach_video(self, stage) -> None:
        """Put a video surface above the list.

        Injected rather than built here, because resolving streams needs the
        YouTube client and this view is deliberately given only the local
        profile. It sits between the header and the scrolling list, so the
        search box stays reachable while something is playing.
        """
        self.video = stage
        self.outer.insertWidget(1, stage)
        stage.closed.connect(self._video_closed)

    def _video_closed(self) -> None:
        self.refresh()

    def _search(self) -> None:
        query = self.search.text().strip()
        if query:
            self.search_requested.emit(query)

    def _maybe_restore(self, text: str) -> None:
        if not text.strip() and self.results:
            self.show_feed(self.ranked)

    def show_results(self, results: list[Scored], note: str = "") -> None:
        """Show a list that did not come from the ranker."""
        self.loading = False
        self.results = results
        self.results_note = note
        self.refresh()

    def show_progress(self, text: str) -> None:
        self.loading = True
        self.progress_text = text
        self.refresh()

    def show_feed(self, ranked: list[Scored]) -> None:
        self.loading = False
        self.ranked = ranked
        self.results = []
        self.results_note = ""
        self.refresh()

    def refresh(self, *_args) -> None:
        self.clear(self.body_layout)

        artists = top_artists(self.tastes, limit=3)
        self.summary.setText(
            "Built from " + ", ".join(name for name, _ in artists)
            if artists else "No listening history yet"
        )

        if self.loading:
            self.body_layout.addWidget(self.empty_label(self.progress_text or "Building your feed…"))
            self.body_layout.addStretch(1)
            return

        if self.results:
            self.body_layout.addWidget(SectionHeading(
                self.results_note or "Results", self.appearance, count=len(self.results)))
            for scored in self.results:
                self.body_layout.addWidget(self._row(scored))
            self.body_layout.addStretch(1)
            return

        if not self.ranked:
            self.body_layout.addWidget(self.empty_label(
                "Nothing here yet.\n\nSearch above, or subscribe to a few channels "
                "and press Rebuild. The ranking runs on this machine — there is "
                "no account and nothing is uploaded."
            ))
            self.body_layout.addStretch(1)
            return

        self.body_layout.addWidget(SectionHeading(
            "Ranked for you", self.appearance, count=len(self.ranked)))

        for scored in self.ranked:
            self.body_layout.addWidget(self._row(scored))

        self.body_layout.addStretch(1)

    def _row(self, scored: Scored) -> QWidget:
        item = scored.candidate

        row = QWidget()
        row.setObjectName("TrackRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # No stylesheet here: the window's sheet already styles #TrackRow, and
        # setting one per row is what made a sixty-row feed slow to draw.

        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(10)

        picture = Thumbnail(item.thumbnail or youtube_thumbnail(item.id),
                            96, 54, self.appearance, glyph="♪")
        layout.addWidget(picture)

        column = QVBoxLayout()
        column.setSpacing(1)

        title = QLabel(item.title)
        title.setObjectName("RowTitle")
        title.setWordWrap(True)
        column.addWidget(title)

        # The "why" is the point: a recommendation you cannot interrogate is
        # one you cannot correct.
        # A search result was not ranked, so it has no "why" and no score to
        # show. Printing "0.00" next to one implies a judgement that was never
        # made.
        why = QLabel(f"{item.artist} · {scored.why}" if scored.why else item.artist)
        why.setObjectName("Subtle")
        column.addWidget(why)
        layout.addLayout(column, 1)

        if scored.terms:
            strength = QLabel(f"{scored.score:.2f}")
            strength.setObjectName("Subtle")
            strength.setToolTip("\n".join(f"{k}: {v:+.2f}" for k, v in scored.terms.items()))
            layout.addWidget(strength)

        liked = self.tastes.likes(item.id)
        like = QPushButton("♥" if liked else "♡")
        like.setObjectName("Quiet")
        like.setToolTip("Like — this shapes your feed")
        like.setProperty("liked", "true" if liked else "false")
        like.clicked.connect(lambda: self.like_toggled.emit(item))
        layout.addWidget(like)

        play = QPushButton("▶")
        play.setObjectName("Quiet")
        play.setToolTip("Watch")
        play.clicked.connect(lambda: self.watch_requested.emit(item))
        layout.addWidget(play)

        listen = QPushButton("♪")
        listen.setObjectName("Quiet")
        listen.setToolTip("Play the audio in the music player")
        listen.clicked.connect(lambda: self.play_requested.emit(item))
        layout.addWidget(listen)

        download = QPushButton("↓")
        download.setObjectName("Quiet")
        download.setToolTip("Download the audio")
        download.clicked.connect(lambda: self.download_requested.emit(item))
        layout.addWidget(download)

        return row


class SubscriptionsView(ScrollingView):
    """Who you follow, kept locally rather than on an account."""

    open_channel = Signal(object)         # Channel
    subscribe_requested = Signal(str)     # link or handle
    unsubscribe = Signal(str)             # channel id
    mute_toggled = Signal(str)            # channel id
    status = Signal(str, str)

    def __init__(self, tastes: Tastes, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        self.tastes = tastes

        title = QLabel("Subscriptions")
        title.setObjectName("Heading")
        self.header_layout.addWidget(title)
        self.header_layout.addStretch(1)

        self.add_field = QLineEdit()
        self.add_field.setPlaceholderText("Channel link or @handle…")
        self.add_field.setFixedWidth(260)
        self.add_field.returnPressed.connect(self._add)
        self.header_layout.addWidget(self.add_field)

        add = QPushButton("Follow")
        add.setObjectName("Primary")
        add.clicked.connect(self._add)
        self.header_layout.addWidget(add)

    def _add(self) -> None:
        text = self.add_field.text().strip()
        if text:
            self.subscribe_requested.emit(text)
            self.add_field.clear()

    def refresh(self, *_args) -> None:
        self.clear(self.body_layout)
        channels = self.tastes.subscriptions()

        note = QLabel(
            "These are yours, not YouTube's. They live in a file on this machine, "
            "so there is no account to sign into and nothing to leak — and no "
            "sync unless you put the folder in something that syncs."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        self.body_layout.addWidget(note)

        if not channels:
            self.body_layout.addWidget(self.empty_label(
                "Not following anything yet.\n\nPaste a channel link above, or "
                "follow an artist from a search result."
            ))
            self.body_layout.addStretch(1)
            return

        self.body_layout.addWidget(SectionHeading(
            "Following", self.appearance, count=len(channels)))

        for channel in channels:
            self.body_layout.addWidget(self._row(channel))
        self.body_layout.addStretch(1)

    def _row(self, channel: Channel) -> QWidget:
        theme = self.appearance.theme

        row = QWidget()
        row.setObjectName("TrackRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setStyleSheet(
            f"#TrackRow {{ background: transparent;"
            f" border-radius: {self.appearance.style.radius}px; }}"
            f"#TrackRow:hover {{ background-color: {theme.panel}; }}"
        )

        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        name = QPushButton(channel.title or channel.id)
        name.setObjectName("Quiet")
        name.clicked.connect(lambda: self.open_channel.emit(channel))
        layout.addWidget(name, 1)

        kind = QLabel(channel.kind)
        kind.setObjectName("Subtle")
        layout.addWidget(kind)

        mute = QPushButton("Muted" if channel.muted else "Mute")
        mute.setObjectName("Quiet")
        mute.setToolTip("Keep following, but leave it out of the feed")
        mute.clicked.connect(lambda: self.mute_toggled.emit(channel.id))
        layout.addWidget(mute)

        remove = QPushButton("✕")
        remove.setObjectName("Quiet")
        remove.clicked.connect(lambda: self.unsubscribe.emit(channel.id))
        layout.addWidget(remove)

        return row
