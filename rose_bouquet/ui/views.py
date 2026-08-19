"""The sections: Library, Albums, Playlists, YouTube Music, Downloads, Import, Server."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core import spotify
from rose_bouquet.core.library import Library
from rose_bouquet.core.playlists import Playlist, PlaylistStore
from rose_bouquet.core.server import MusicServer
from rose_bouquet.ui.theme import Appearance
from rose_bouquet.ui.widgets import Card, SectionHeading, TrackRow

logger = logging.getLogger(__name__)


#: Library rows built per block. Small enough that a block is imperceptible,
#: large enough that a fast scroll does not outrun it.
LIBRARY_CHUNK = 150


class ScrollingView(QWidget):
    """A scrolling column, which nearly every section is."""

    def __init__(self, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.appearance = appearance

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        #: For widgets that must outlive a refresh. `body_layout` is cleared on
        #: every redraw, and anything the user types into must not be in there —
        #: a text field that is deleted and re-added loses its contents and its
        #: focus, and if it was built once in __init__ it simply disappears.
        self.outer = outer

        self.header = QWidget()
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(18, 14, 18, 8)
        self.header_layout.setSpacing(10)
        outer.addWidget(self.header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 0, 14, 24)
        self.body_layout.setSpacing(2)
        scroll.setWidget(self.body)
        outer.addWidget(scroll, 1)
        #: Kept, so a view can hide its list to give the space to something
        #: else — the Shorts reel takes the whole view this way.
        self.scroll = scroll

        #: The track currently playing, so the highlight survives a redraw
        #: that was not told about it.
        self.playing_path = ""

    @staticmethod
    def clear(layout) -> None:
        """Empty a layout and let the widgets go.

        `deleteLater` alone leaves them alive — and parented — until the event
        loop next runs, so a burst of rebuilds can pile up thousands of live
        widgets before any are freed. Unparenting first drops them out of the
        tree immediately, and the deferred delete then only has to free them.
        """
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def empty_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Subtle")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setContentsMargins(20, 50, 20, 0)
        return label

    def stage_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance

    def apply_appearance(self, appearance: Appearance) -> None:
        self.stage_appearance(appearance)
        self.refresh()

    def refresh(self, *_args) -> None:
        raise NotImplementedError

    # ── Building a long list in blocks ────────────────────────────
    #
    # A thousand tracks or six hundred album covers is a second of Qt making
    # widgets nobody has scrolled to yet. Views with that problem call
    # `paged()` once, then answer `_more_to_build()` and implement `_extend()`;
    # the block after the one on screen arrives as the scroll reaches for it.

    def paged(self) -> None:
        """Build this view's list in blocks, as it is scrolled."""
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_extend)

    def _more_to_build(self) -> bool:
        return False

    def _extend(self) -> None:
        raise NotImplementedError

    def _fill_viewport(self) -> None:
        """A window taller than one block would otherwise sit there with no
        scrollbar to move and nothing more coming."""
        if self._more_to_build() and self.scroll.verticalScrollBar().maximum() <= 0:
            self._extend()

    def _maybe_extend(self, value: int) -> None:
        """Another block once the scroll is within a screenful of the end."""
        if not self._more_to_build():
            return
        bar = self.scroll.verticalScrollBar()
        if value >= bar.maximum() - bar.pageStep():
            self._extend()

    def set_playing(self, playing_path: str) -> None:
        """Move the highlight to whatever is playing now.

        A track change used to go through `refresh`, which threw away every
        row widget in the view and made them all again — a visible stall on a
        large library, and it dropped your scroll position on the floor every
        time a song ended. Which row is lit is one property on at most two
        rows, so that is all this touches. A view whose *contents* really do
        depend on what is playing can still override it.
        """
        if playing_path == self.playing_path:
            return
        self.playing_path = playing_path
        for row in self.body.findChildren(TrackRow):
            row.set_playing(row.track.path == playing_path)


# ── Library ───────────────────────────────────────────────────────

class LibraryView(ScrollingView):
    """Every track, searchable."""

    play_requested = Signal(object, object)     # Track, list[Track]
    menu_requested = Signal(object, object)
    scan_requested = Signal()
    #: Play everything currently listed, shuffled or in order. Carries the
    #: list rather than the library, so a search narrows what Play acts on —
    #: which is what someone who has just typed "shoegaze" means by it.
    play_all_requested = Signal(object, bool)   # list[Track], shuffle?

    def __init__(self, library: Library, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        self.library = library

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search your library…")
        self.search.setClearButtonEnabled(True)
        # Typing "midwest emo" rebuilt the list eleven times, ten of them for
        # results nobody read. Rebuilds now wait for a pause in the typing.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(160)
        self._search_timer.timeout.connect(lambda: self.refresh(self.playing_path))
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.header_layout.addWidget(self.search, 1)

        self.count = QLabel()
        self.count.setObjectName("Subtle")
        self.header_layout.addWidget(self.count)

        self.play_all = QPushButton("▶  Play all")
        self.play_all.setObjectName("Primary")
        self.play_all.setToolTip("Play everything listed, in order")
        self.play_all.clicked.connect(
            lambda: self.play_all_requested.emit(self._tracks, False))
        self.header_layout.addWidget(self.play_all)

        self.shuffle_all = QPushButton("⇄  Shuffle")
        self.shuffle_all.setObjectName("Quiet")
        self.shuffle_all.setToolTip("Play everything listed, in a shuffled order")
        self.shuffle_all.clicked.connect(
            lambda: self.play_all_requested.emit(self._tracks, True))
        self.header_layout.addWidget(self.shuffle_all)

        scan = QPushButton("Rescan")
        scan.setObjectName("Quiet")
        scan.setToolTip("Look for new music on disk")
        scan.clicked.connect(self.scan_requested.emit)
        self.header_layout.addWidget(scan)

        #: The whole result, and how much of it has been built into widgets.
        #: The list is all of it; the widgets arrive as you reach them.
        self._tracks: list = []
        self._built = 0
        self.paged()

    def refresh(self, playing_path: str = "") -> None:
        self.playing_path = playing_path or self.playing_path
        self.clear(self.body_layout)

        tracks = self.library.search(self.search.text())
        self.count.setText(f"{len(tracks)} track{'' if len(tracks) == 1 else 's'}")

        # A library full of tracks whose files are not reachable looks exactly
        # like a working one until you press play on the first of them. Saying
        # so at the top of the list is the difference between "this app is
        # broken" and "that drive is not mounted".
        absent = self.library.missing_roots()
        if absent:
            for root in absent:
                self.body_layout.addWidget(self._missing_folder_notice(root))

        self._tracks = tracks
        self._built = 0
        for button in (self.play_all, self.shuffle_all):
            button.setEnabled(bool(tracks))

        if not tracks:
            message = (
                "No music found.\n\nAdd a folder in Settings → Library, or download "
                "something from YouTube Music."
                if not self.library.tracks else "Nothing matches that search."
            )
            self.body_layout.addWidget(self.empty_label(message))
            self.body_layout.addStretch(1)
            return

        self.body_layout.addStretch(1)
        self._extend()

    def _missing_folder_notice(self, root) -> QLabel:
        notice = QLabel(
            f"{root} is not there. The tracks below are still listed, and "
            "will play again once that folder is back — nothing has been "
            "removed from your library."
        )
        notice.setObjectName("Subtle")
        notice.setWordWrap(True)
        notice.setContentsMargins(10, 8, 10, 8)
        return notice

    def _extend(self) -> None:
        """Build the next block of rows, above the trailing stretch.

        The list used to stop at 500 with "search to narrow it down", on the
        reasoning that nobody scrolls a thousand tracks. That is a guess about
        the person using it, and the honest reading of someone scrolling to the
        bottom is that they wanted what was down there. The cap was really
        about build cost — a thousand row widgets at once is a visible stall —
        so the cost is what got fixed: every track is in the list, and the
        widgets for them are made in blocks as the scroll reaches them.
        """
        start, end = self._built, min(self._built + LIBRARY_CHUNK, len(self._tracks))
        if start >= end:
            return

        # Everything after the rows is the stretch, so new rows go before it.
        at = self.body_layout.count() - 1
        for track in self._tracks[start:end]:
            row = TrackRow(track, self.appearance,
                           playing=track.path == self.playing_path)
            row.play_requested.connect(
                lambda t: self.play_requested.emit(t, self._tracks))
            row.menu_requested.connect(self.menu_requested.emit)
            self.body_layout.insertWidget(at, row)
            at += 1

        self._built = end
        QTimer.singleShot(0, self._fill_viewport)

    def _more_to_build(self) -> bool:
        return self._built < len(self._tracks)


class AlbumsView(ScrollingView):
    """Albums as a wall of covers, opening into their tracks."""

    play_requested = Signal(object, object)
    menu_requested = Signal(object, object)
    #: (artist, album) — go and look up what this album actually contains.
    tracklist_wanted = Signal(object)
    #: title, artist, album — a track of it you do not have, go and get it.
    fetch_requested = Signal(str, str, str)

    COLUMNS = 5
    #: Covers built per block — a few rows of the grid at a time.
    CHUNK = 60

    def __init__(self, library: Library, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        self.library = library
        self.open_album: Optional[tuple[str, str]] = None
        self.release = None

        self.title = QLabel("Albums")
        self.title.setObjectName("Heading")
        self.header_layout.addWidget(self.title)
        self.header_layout.addStretch(1)

        self.back = QPushButton("← All albums")
        self.back.setObjectName("Quiet")
        self.back.clicked.connect(lambda: self.show_album(None))
        self.back.setVisible(False)
        self.header_layout.addWidget(self.back)

        #: The whole wall, and how much of it has been made into cards. Six
        #: hundred covers built at once was over half a second of nothing
        #: happening every time this tab was opened.
        self._albums: list = []
        self._built = 0
        self._grid = None
        self.paged()

    def show_album(self, key: Optional[tuple[str, str]]) -> None:
        self.open_album = key
        #: The catalogue's tracklist for the open album, once it arrives.
        self.release = None
        #: Whether an answer — including "nobody knows this album" — has come
        #: back yet. Without it, "still looking" and "nothing found" look the
        #: same on screen, which is how the whole feature read as broken.
        self.looked_up = False
        self.refresh()
        if key is not None:
            self.tracklist_wanted.emit(key)

    def show_tracklist(self, key, release) -> None:
        """The lookup answered. Ignored if you have opened another album."""
        if key != self.open_album:
            return
        self.release = release
        self.looked_up = True
        self.refresh()

    def _render_album(self, tracks: list, playing_path: str) -> None:
        """One album: everything on it, and which of it you have.

        The list used to be the files on disk, so an album you had four tracks
        of looked like a four-track album — there was no way to tell an EP from
        half a record, which is the one thing you want to know while looking at
        it. The catalogue supplies the shape; the files fill it in.

        Until the lookup comes back, and whenever it fails, this is exactly the
        old list. A missing tracklist must never cost you the view of your own
        music.
        """
        from rose_bouquet.core import musicbrainz

        artist, album = self.open_album
        self.title.setText(album)
        self.back.setVisible(True)

        slots = musicbrainz.reconcile(self.release, tracks)
        owned = sum(1 for slot in slots if slot.owned)
        missing = len(slots) - owned

        heading = f"{artist} · {owned} of {len(slots)} tracks" if missing \
            else f"{artist} · {len(slots)} tracks"
        self.body_layout.addWidget(SectionHeading(heading, self.appearance))

        for slot in slots:
            if slot.owned:
                row = TrackRow(slot.track, self.appearance, index=slot.position,
                               show_art=False, show_album=False,
                               playing=slot.track.path == playing_path)
                row.play_requested.connect(
                    lambda t, ts=tracks: self.play_requested.emit(t, ts))
                row.menu_requested.connect(self.menu_requested.emit)
                self.body_layout.addWidget(row)
            else:
                self.body_layout.addWidget(self._missing_row(slot, artist, album))

        if missing:
            note = QLabel(
                f"{missing} track{'' if missing == 1 else 's'} of this album "
                f"{'is' if missing == 1 else 'are'} not in your library."
            )
        elif self.release is not None:
            note = QLabel("You have all of this album.")
        elif self.looked_up:
            # Said out loud, because an album with no tracklist looks exactly
            # like an album whose tracklist never loaded.
            note = QLabel(
                "Neither MusicBrainz nor YouTube Music has a tracklist for "
                "this one, so this is what is on disk. Small and self-released "
                "records often are not catalogued anywhere."
            )
        else:
            note = QLabel("Looking up the full tracklist…")

        note.setObjectName("Subtle")
        note.setWordWrap(True)
        note.setContentsMargins(12, 8, 12, 0)
        self.body_layout.addWidget(note)

        self.body_layout.addStretch(1)

    def _missing_row(self, slot, artist: str, album: str) -> QWidget:
        """A track the album has and you do not, with a way to go and get it."""
        row = QWidget()
        row.setObjectName("TrackRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        dim = self.appearance.theme.text_dim

        number = QLabel(str(slot.position))
        number.setFixedWidth(24)
        number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        number.setStyleSheet(f"color: {dim}; background: transparent;")
        layout.addWidget(number)

        title = QLabel(slot.title)
        title.setStyleSheet(f"color: {dim}; background: transparent;")
        layout.addWidget(title, 1)

        if slot.duration:
            length = QLabel(f"{slot.duration // 60}:{slot.duration % 60:02d}")
            length.setStyleSheet(f"color: {dim}; background: transparent;")
            layout.addWidget(length)

        get = QPushButton("Get")
        get.setObjectName("Quiet")
        get.setToolTip("Find this on YouTube Music and download it")
        get.clicked.connect(
            lambda: self.fetch_requested.emit(slot.title, artist or "", album))
        layout.addWidget(get)

        return row

    def refresh(self, playing_path: str = "") -> None:
        self.playing_path = playing_path or self.playing_path
        playing_path = self.playing_path
        self.clear(self.body_layout)
        albums = self.library.albums()

        self._grid = None
        self._albums = []
        self._built = 0

        if self.open_album is not None and self.open_album in albums:
            self._render_album(albums[self.open_album], playing_path)
            return

        self.title.setText("Albums")
        self.back.setVisible(False)

        if not albums:
            self.body_layout.addWidget(self.empty_label("No albums yet."))
            self.body_layout.addStretch(1)
            return

        grid_holder = QWidget()
        self._grid = QGridLayout(grid_holder)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)

        self._albums = list(albums.items())
        self._built = 0

        self.body_layout.addWidget(grid_holder)
        self.body_layout.addStretch(1)
        self._extend()

    def _more_to_build(self) -> bool:
        return self._grid is not None and self._built < len(self._albums)

    def _extend(self) -> None:
        """Make the next row of covers, and no more than that."""
        start, end = self._built, min(self._built + self.CHUNK, len(self._albums))
        if self._grid is None or start >= end:
            return

        for index in range(start, end):
            (artist, album), tracks = self._albums[index]
            card = Card(
                (artist, album), album, artist,
                tracks[0].cover if tracks else "", self.appearance,
            )
            card.activated.connect(self.show_album)
            self._grid.addWidget(card, index // self.COLUMNS, index % self.COLUMNS)

        self._built = end
        QTimer.singleShot(0, self._fill_viewport)


# ── Playlists ─────────────────────────────────────────────────────

class PlaylistsView(ScrollingView):
    """Playlists, and the tracks in the open one."""

    play_requested = Signal(object, object)
    menu_requested = Signal(object, object)
    changed = Signal()

    def __init__(self, library: Library, store: PlaylistStore, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        self.library = library
        self.store = store
        self.open_playlist: Optional[Playlist] = None

        self.title = QLabel("Playlists")
        self.title.setObjectName("Heading")
        self.header_layout.addWidget(self.title)
        self.header_layout.addStretch(1)

        self.new_field = QLineEdit()
        self.new_field.setPlaceholderText("New playlist…")
        self.new_field.setFixedWidth(180)
        self.new_field.returnPressed.connect(self._create)
        self.header_layout.addWidget(self.new_field)

        self.back = QPushButton("← All playlists")
        self.back.setObjectName("Quiet")
        self.back.clicked.connect(lambda: self.show_playlist(None))
        self.back.setVisible(False)
        self.header_layout.addWidget(self.back)

    def _create(self) -> None:
        title = self.new_field.text().strip()
        if not title:
            return
        self.store.create(title)
        self.new_field.clear()
        self.refresh()
        self.changed.emit()

    def show_playlist(self, playlist: Optional[Playlist]) -> None:
        self.open_playlist = playlist
        self.refresh()

    def refresh(self, playing_path: str = "") -> None:
        self.playing_path = playing_path or self.playing_path
        playing_path = self.playing_path
        self.clear(self.body_layout)
        playlists = self.store.all(self.library)

        if self.open_playlist is not None:
            # Re-read it, in case it changed on disk since it was opened.
            current = next(
                (p for p in playlists if str(p.path) == str(self.open_playlist.path)), None
            )
            if current is not None:
                self._render_playlist(current, playing_path)
                return
            self.open_playlist = None

        self.title.setText("Playlists")
        self.back.setVisible(False)
        self.new_field.setVisible(True)

        if not playlists:
            self.body_layout.addWidget(self.empty_label(
                "No playlists yet.\n\nName one above, or import a Spotify playlist."
            ))
            self.body_layout.addStretch(1)
            return

        for playlist in playlists:
            self.body_layout.addWidget(self._playlist_row(playlist))
        self.body_layout.addStretch(1)

    def _playlist_row(self, playlist: Playlist) -> QWidget:
        row = QWidget()
        row.setObjectName("TrackRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        open_button = QPushButton(playlist.title)
        open_button.setObjectName("Quiet")
        open_button.clicked.connect(lambda: self.show_playlist(playlist))
        layout.addWidget(open_button, 1)

        summary = QLabel(f"{len(playlist.tracks)} tracks · {playlist.clock}")
        summary.setObjectName("Subtle")
        layout.addWidget(summary)

        if playlist.missing:
            missing = QLabel(f"{len(playlist.missing)} missing")
            missing.setStyleSheet(
                f"color: {self.appearance.theme.warning}; background: transparent;"
            )
            missing.setToolTip("\n".join(playlist.missing[:40]))
            layout.addWidget(missing)

        play = QPushButton("▶")
        play.setObjectName("Quiet")
        play.setEnabled(bool(playlist.tracks))
        play.clicked.connect(
            lambda: playlist.tracks and self.play_requested.emit(playlist.tracks[0], playlist.tracks)
        )
        layout.addWidget(play)

        delete = QPushButton("✕")
        delete.setObjectName("Quiet")
        delete.clicked.connect(lambda: self._delete(playlist))
        layout.addWidget(delete)

        return row

    def _render_playlist(self, playlist: Playlist, playing_path: str) -> None:
        self.open_playlist = playlist
        self.title.setText(playlist.title)
        self.back.setVisible(True)
        self.new_field.setVisible(False)

        self.body_layout.addWidget(SectionHeading(
            f"{len(playlist.tracks)} tracks · {playlist.clock}", self.appearance,
            action=("Play all", lambda: playlist.tracks and self.play_requested.emit(
                playlist.tracks[0], playlist.tracks)),
        ))

        for number, track in enumerate(playlist.tracks, start=1):
            row = TrackRow(track, self.appearance, index=number, show_art=False,
                           playing=track.path == playing_path)
            row.play_requested.connect(
                lambda t, ts=playlist.tracks: self.play_requested.emit(t, ts))
            row.menu_requested.connect(self.menu_requested.emit)
            self.body_layout.addWidget(row)

        if playlist.missing:
            self.body_layout.addWidget(SectionHeading(
                "Not found when this was imported", self.appearance,
                count=len(playlist.missing),
            ))
            note = QLabel(
                "These were in the source playlist but could not be matched. "
                "Search for them by hand, or try the import again — YouTube "
                "Music's catalogue changes."
            )
            note.setObjectName("Subtle")
            note.setWordWrap(True)
            self.body_layout.addWidget(note)

            for line in playlist.missing:
                label = QLabel(f"·  {line}")
                label.setStyleSheet(
                    f"color: {self.appearance.theme.warning}; background: transparent;"
                    f" padding: 2px 12px;"
                )
                self.body_layout.addWidget(label)

        self.body_layout.addStretch(1)

    def _delete(self, playlist: Playlist) -> None:
        self.store.delete(playlist)
        if self.open_playlist is playlist:
            self.open_playlist = None
        self.refresh()
        self.changed.emit()


# ── YouTube Music ─────────────────────────────────────────────────

class DownloadsView(ScrollingView):
    """What is downloading, what finished, and what failed.

    Rows are built once and updated in place. The first version rebuilt every
    row on every progress tick, which is fine for one download and lethal for a
    playlist import: three concurrent downloads reporting progress several times
    a second, each rebuild throwing away and recreating hundreds of widgets. The
    window stopped responding long before the downloads finished.

    Updates are also coalesced onto a timer, so a burst of progress from several
    downloads costs one repaint rather than one each.
    """

    retry_requested = Signal(object)

    #: Repaint at most this often. Faster than the eye needs, slower than
    #: yt-dlp reports.
    FLUSH_MS = 200

    def __init__(self, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        #: key → (label, fraction, state, payload)
        self.entries: dict[str, tuple[str, float, str, object]] = {}
        #: key → the widgets to update, so nothing is rebuilt needlessly
        self.rows: dict[str, dict] = {}
        self._dirty: set[str] = set()

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(self.FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush)

        title = QLabel("Downloads")
        title.setObjectName("Heading")
        self.header_layout.addWidget(title)

        self.summary = QLabel()
        self.summary.setObjectName("Subtle")
        self.header_layout.addWidget(self.summary)
        self.header_layout.addStretch(1)

        clear = QPushButton("Clear finished")
        clear.setObjectName("Quiet")
        clear.clicked.connect(self.clear_finished)
        self.header_layout.addWidget(clear)

    # ── Updating ──────────────────────────────────────────────────

    def note(self, key: str, label: str, fraction: float, state: str, payload=None) -> None:
        """Record progress. Cheap enough to call as often as you like."""
        self.entries[key] = (label, fraction, state, payload)
        self._dirty.add(key)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def clear_finished(self) -> None:
        for key, (_label, _fraction, state, _payload) in list(self.entries.items()):
            if state == "done":
                del self.entries[key]
        self.refresh()

    def _flush(self) -> None:
        """Apply whatever changed since the last repaint."""
        if set(self.rows) != set(self.entries):
            # The set of downloads changed, so the list is rebuilt. This happens
            # once per new download, not once per progress tick.
            self.refresh()
            return

        for key in self._dirty:
            self._update_row(key)
        self._dirty.clear()
        self._update_summary()

    def _update_summary(self) -> None:
        states = [state for _l, _f, state, _p in self.entries.values()]
        running = sum(1 for s in states if s in ("queued", "downloading", "converting"))
        done = sum(1 for s in states if s == "done")
        failed = sum(1 for s in states if s == "failed")

        parts = []
        if running:
            parts.append(f"{running} in progress")
        if done:
            parts.append(f"{done} done")
        if failed:
            parts.append(f"{failed} failed")
        self.summary.setText(" · ".join(parts))

    def _update_row(self, key: str) -> None:
        row = self.rows.get(key)
        entry = self.entries.get(key)
        if row is None or entry is None:
            return

        label, fraction, state, payload = entry
        row["name"].setText(label)
        row["status"].setText(state)
        row["status"].setStyleSheet(
            f"color: {self.appearance.theme.error if state == 'failed' else self.appearance.theme.text_dim};"
        )

        bar = row["bar"]
        finished = state in ("done", "failed")
        bar.setVisible(not finished)
        if not finished:
            bar.setValue(int(fraction * 100))

        row["retry"].setVisible(state == "failed" and payload is not None)

    # ── Drawing ───────────────────────────────────────────────────

    def refresh(self, *_args) -> None:
        self.clear(self.body_layout)
        self.rows = {}
        self._dirty.clear()

        if not self.entries:
            self.summary.clear()
            self.body_layout.addWidget(self.empty_label(
                "Nothing downloading.\n\nFind something in YouTube Music and press "
                "Download, or import a Spotify playlist."
            ))
            self.body_layout.addStretch(1)
            return

        for key in self.entries:
            widget, parts = self._build_row(key)
            self.rows[key] = parts
            self.body_layout.addWidget(widget)
            self._update_row(key)

        self._update_summary()
        self.body_layout.addStretch(1)

    def _build_row(self, key: str) -> tuple[QWidget, dict]:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(4)

        top = QHBoxLayout()
        name = QLabel()
        name.setStyleSheet(f"color: {self.appearance.theme.text};")
        top.addWidget(name, 1)

        status = QLabel()
        status.setObjectName("Subtle")
        top.addWidget(status)

        retry = QPushButton("Retry")
        retry.setObjectName("Quiet")
        def retry_this(_checked: bool = False, key: str = key) -> None:
            # The entry may have been cleared between the click and the slot.
            entry = self.entries.get(key)
            if entry is not None and entry[3] is not None:
                self.retry_requested.emit(entry[3])

        retry.clicked.connect(retry_this)
        retry.setVisible(False)
        top.addWidget(retry)
        layout.addLayout(top)

        bar = QProgressBar()
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        layout.addWidget(bar)

        return row, {"name": name, "status": status, "bar": bar, "retry": retry}


# ── Spotify import ────────────────────────────────────────────────

class ImportView(ScrollingView):
    """Import a Spotify playlist, and say plainly what did not come across."""

    import_requested = Signal(str, str, bool)    # link, pasted text, download?
    resume_requested = Signal(object)            # ImportJob
    force_resume_requested = Signal(object)      # ImportJob, ignoring the wait
    retry_failed_requested = Signal()            # try the failed downloads again
    status = Signal(str, str)

    def __init__(self, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        self.report: Optional[spotify.ImportReport] = None
        self.busy = False
        self.progress_text = ""
        #: Imports found on disk with work left, offered for resuming.
        self.unfinished: list = []

        title = QLabel("Import from Spotify")
        title.setObjectName("Heading")
        self.header_layout.addWidget(title)
        self.header_layout.addStretch(1)

        self.outer.insertWidget(1, self._form())
        self.refresh()

    def show_unfinished(self, jobs: list) -> None:
        """Offer to pick up an import that was cut short."""
        self.unfinished = jobs
        self.refresh()

    def _form(self) -> QWidget:
        """The part you type into — built once and never rebuilt."""
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(18, 4, 18, 10)
        layout.setSpacing(8)

        explain = QLabel(
            "Spotify does not let anything take the audio, so an import brings "
            "the track list across and finds each song on YouTube Music. Most "
            "match. The ones that do not are listed below rather than quietly "
            "dropped — that list is the whole point."
        )
        explain.setObjectName("Subtle")
        explain.setWordWrap(True)
        layout.addWidget(explain)

        self.link = QLineEdit()
        self.link.setPlaceholderText("Paste a playlist link — https://open.spotify.com/playlist/…")
        self.link.setClearButtonEnabled(True)
        self.link.returnPressed.connect(lambda: self._go(download=False))
        layout.addWidget(self.link)

        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText(
            "…or paste a track list, one per line:\n"
            "Artist - Title\n"
            "Artist - Title\n\n"
            "An Exportify CSV works too — paste it, or use Choose CSV below."
        )
        self.paste.setMaximumHeight(130)
        layout.addWidget(self.paste)

        buttons = QHBoxLayout()

        self.csv_button = QPushButton("Choose CSV…")
        self.csv_button.setToolTip("Load an Exportify CSV into the box above")
        self.csv_button.clicked.connect(self._browse_csv)
        buttons.addWidget(self.csv_button)

        buttons.addStretch(1)

        self.match_button = QPushButton("Match only")
        self.match_button.setToolTip("Find them on YouTube Music without downloading")
        self.match_button.clicked.connect(lambda: self._go(download=False))
        buttons.addWidget(self.match_button)

        self.fetch_button = QPushButton("Match and download")
        self.fetch_button.setObjectName("Primary")
        self.fetch_button.clicked.connect(lambda: self._go(download=True))
        buttons.addWidget(self.fetch_button)

        layout.addLayout(buttons)

        return form

    def _browse_csv(self) -> None:
        """Load an Exportify CSV into the paste box, which already parses one."""
        from PySide6.QtWidgets import QFileDialog

        chosen, _ = QFileDialog.getOpenFileName(
            self, "Your Exportify CSV", str(Path.home() / "Downloads"),
            "Track lists (*.csv *.txt);;All files (*)",
        )
        if chosen:
            self._load_csv(chosen)

    def _load_csv(self, chosen: str) -> None:
        """The reading half, kept apart from the dialog so it can be tested."""
        try:
            # utf-8-sig: Exportify writes a BOM, and the header row is what the
            # parser sniffs to tell a CSV from a plain list — a BOM hides it.
            text = Path(chosen).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as problem:
            logger.warning("could not read %s: %s", chosen, problem)
            self.status.emit(f"Could not read that file — {problem.strerror}", "warning")
            return
        if not text.strip():
            self.status.emit("That file is empty", "warning")
            return
        self.paste.setPlainText(text)
        lines = max(0, len(text.strip().splitlines()) - 1)
        self.status.emit(
            f"Loaded {Path(chosen).name} — {lines} rows. Now Match, or Match and download.",
            "success")

    def _go(self, *, download: bool) -> None:
        if self.busy:
            return
        link = self.link.text().strip()
        text = self.paste.toPlainText().strip()
        if not link and not text:
            self.status.emit("Paste a playlist link or a track list first", "warning")
            return
        self.busy = True
        self.progress_text = "Reading the playlist…"
        self.refresh()
        self.import_requested.emit(link, text, download)

    def show_report(self, report: Optional[spotify.ImportReport]) -> None:
        self.busy = False
        self.report = report
        self.refresh()

    def show_progress(self, text: str) -> None:
        self.progress_text = text
        # Only the label changes, so this does not rebuild the whole view.
        if hasattr(self, "progress_label") and self.progress_label is not None:
            self.progress_label.setText(text)

    def refresh(self, *_args) -> None:
        self.clear(self.body_layout)
        self.progress_label = None

        # The form is persistent chrome; only the results are redrawn.
        for button in (getattr(self, "match_button", None), getattr(self, "fetch_button", None),
                       getattr(self, "csv_button", None)):
            if button is not None:
                button.setEnabled(not self.busy)

        if self.busy:
            self.progress_label = QLabel(self.progress_text or "Working…")
            self.progress_label.setObjectName("Subtle")
            self.body_layout.addWidget(self.progress_label)

        for job in self.unfinished:
            self.body_layout.addWidget(self._unfinished_row(job))

        if self.report is not None:
            self._render_report(self.report)

        self.body_layout.addStretch(1)

    def _unfinished_row(self, job) -> QWidget:
        """One interrupted import, with a button to carry on."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        label = QLabel(f"“{job.title}” — {job.summary}")
        label.setStyleSheet(f"color: {self.appearance.theme.warning};")
        label.setWordWrap(True)
        layout.addWidget(label, 1)

        resume = QPushButton("Carry on")
        resume.setObjectName("Primary")
        resume.setToolTip("Download what this import still owes, skipping anything you already have")
        resume.clicked.connect(lambda _c=False, job=job: self.resume_requested.emit(job))
        layout.addWidget(resume)

        # The rate limit belongs to the connection, not to the playlist. If you
        # have moved to another network — a hotspot, a VPN — our own note about
        # waiting is the only thing in the way, and it should not be.
        if job.wait_remaining():
            anyway = QPushButton("Try now")
            anyway.setToolTip(
                "Ignore the wait and ask Spotify again.\n\n"
                "Worth pressing if you have changed network since — the limit "
                "is on the connection, not on your account."
            )
            anyway.clicked.connect(
                lambda _c=False, job=job: self.force_resume_requested.emit(job))
            layout.addWidget(anyway)

        return row

    def _render_report(self, report: spotify.ImportReport) -> None:
        self.body_layout.addWidget(SectionHeading(
            report.title or "Imported", self.appearance))

        summary = QLabel(report.summary)
        summary.setStyleSheet(
            f"color: {self.appearance.theme.success if not report.missed else self.appearance.theme.warning};"
            f" font-weight: 600; background: transparent;"
        )
        self.body_layout.addWidget(summary)

        if not report.missed and not report.failed:
            return

        # Not found and failed-to-download are one list with two reasons: both
        # mean the song is not in your library, and that is what the list is
        # for. Kept as separate headings so the second group stays actionable —
        # those already matched, so a retry is worth offering.
        if report.missed:
            self.body_layout.addWidget(SectionHeading(
                "Not found", self.appearance, count=len(report.missed)))
            for track in report.missed:
                self.body_layout.addWidget(self._miss_row(str(track)))

        if report.failed:
            self.body_layout.addWidget(SectionHeading(
                "Failed to download", self.appearance, count=len(report.failed)))
            for track, why in report.failed:
                self.body_layout.addWidget(
                    self._miss_row(str(track), why or "the download did not finish"))

            retry = QPushButton(f"Retry {len(report.failed)} downloads")
            retry.setObjectName("Primary")
            retry.clicked.connect(self.retry_failed_requested.emit)
            row = QHBoxLayout()
            row.setContentsMargins(12, 4, 12, 0)
            row.addWidget(retry)
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            self.body_layout.addWidget(holder)

        note = QLabel(
            "These are saved with the playlist, so they are still listed the "
            "next time you open it."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        self.body_layout.addWidget(note)

    def _miss_row(self, text: str, why: str = "") -> QWidget:
        """One line of what did not arrive, with the reason under it."""
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(12, 1, 12, 1)
        column.setSpacing(0)

        label = QLabel(f"·  {text}")
        label.setStyleSheet(
            f"color: {self.appearance.theme.warning}; background: transparent;")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(label)

        if why:
            reason = QLabel(f"    {why}")
            reason.setObjectName("Subtle")
            reason.setWordWrap(True)
            reason.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column.addWidget(reason)

        return row


# ── Server ────────────────────────────────────────────────────────

class ServerView(ScrollingView):
    """Hosting the library to other devices."""

    toggled = Signal(bool)
    status = Signal(str, str)

    def __init__(self, server: MusicServer, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        self.server = server

        title = QLabel("Serve to other devices")
        title.setObjectName("Heading")
        self.header_layout.addWidget(title)
        self.header_layout.addStretch(1)

    def refresh(self, *_args) -> None:
        self.clear(self.body_layout)
        config = self.server.config
        running = self.server.running

        explain = QLabel(
            "Rose Bouquet can serve your library over the local network using the "
            "Subsonic API — the one every third-party music client speaks. Point "
            "the Rose Bouquet Android app at it, or Symfonium, DSub, substreamer, "
            "Feishin, or anything else that talks Subsonic."
        )
        explain.setObjectName("Subtle")
        explain.setWordWrap(True)
        self.body_layout.addWidget(explain)

        state = QLabel("Serving" if running else "Not serving")
        state.setStyleSheet(
            f"color: {self.appearance.theme.success if running else self.appearance.theme.text_dim};"
            f" font-weight: 700; font-size: {self.appearance.style.heading_size}px;"
            f" background: transparent;"
        )
        self.body_layout.addWidget(state)

        button = QPushButton("Stop serving" if running else "Start serving")
        button.setObjectName("Primary")
        button.clicked.connect(lambda: self.toggled.emit(not running))
        self.body_layout.addWidget(button)

        if running:
            self.body_layout.addWidget(SectionHeading("Connect with", self.appearance))
            for label, value in (
                ("Server", config.url),
                ("Username", config.username),
                ("Password", config.password or "(none — anyone on the network can connect)"),
                ("Tracks shared", str(len(self.server.library.tracks))),
            ):
                row = QLabel(f"{label}:  {value}")
                row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                row.setStyleSheet(
                    f"color: {self.appearance.theme.text}; background: transparent; padding: 2px 0;"
                )
                self.body_layout.addWidget(row)

            warning = QLabel(
                "This is a local-network server. It has no HTTPS and is not built "
                "to face the internet — if you want it reachable from outside, put "
                "it behind a reverse proxy you trust."
            )
            warning.setStyleSheet(
                f"color: {self.appearance.theme.warning}; background: transparent;"
            )
            warning.setWordWrap(True)
            self.body_layout.addWidget(warning)

        self.body_layout.addStretch(1)
