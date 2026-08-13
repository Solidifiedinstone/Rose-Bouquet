"""The shell: a nav rail, a section, a queue, and the player across the bottom.

The player bar is the only thing always on screen, so it is the only thing that
gets to be complicated: art, title, transport, a seek bar, the visualiser, a
volume slider and the queue toggle. Everything else is a section that can be
swapped out without it noticing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core import imports, spotify, takeout, ytmusic
from rose_bouquet.core import youtube as yt
from rose_bouquet.core.library import Library, Track, data_dir
from rose_bouquet.core.playlists import PlaylistStore
from rose_bouquet.core.playqueue import Repeat
from rose_bouquet.core.recommend import Candidate, rank
from rose_bouquet.core.server import MusicServer
from rose_bouquet.core.tastes import Channel, Tastes
from rose_bouquet.ui import tasks
from rose_bouquet.ui.branding import APP_NAME
from rose_bouquet.ui.feed_views import FeedView, SubscriptionsView
from rose_bouquet.ui.first_run import FirstRunDialog
from rose_bouquet.ui.playback import Playback
from rose_bouquet.ui.preferences import Preferences
from rose_bouquet.ui.settings import SettingsDialog
from rose_bouquet.ui.theme import Appearance, set_active_style
from rose_bouquet.ui.views import (
    AlbumsView,
    DownloadsView,
    ImportView,
    LibraryView,
    PlaylistsView,
    ServerView,
    YouTubeView,
)
from rose_bouquet.ui.visualizer import Visualizer
from rose_bouquet.ui.watch import WatchView
from rose_bouquet.ui.widgets import Banner, CoverArt

logger = logging.getLogger(__name__)

SECTIONS = [
    ("feed", "For you", "✦"),
    ("watch", "Watch", "▶"),
    ("subscriptions", "Following", "☆"),
    ("library", "Library", "♫"),
    ("albums", "Albums", "▣"),
    ("playlists", "Playlists", "≡"),
    ("youtube", "YouTube Music", "▶"),
    ("import", "Import", "⤓"),
    ("downloads", "Downloads", "↓"),
    ("server", "Serve", "⇄"),
]


class MainWindow(QMainWindow):
    """Rose Bouquet."""

    def __init__(self, preferences: Optional[Preferences] = None) -> None:
        super().__init__()

        self.preferences = preferences or Preferences.load()
        self.appearance = self.preferences.appearance()
        set_active_style(self.appearance.style)

        self.library = Library.load()
        if self.preferences.folders:
            self.library.folders = list(self.preferences.folders)

        self.playlists = PlaylistStore()
        self.ytmusic = ytmusic.YouTubeMusic()
        self.youtube = yt.YouTube()
        self.tastes = Tastes.load()
        self.playback = Playback(self.library, self)
        self.downloads_pool = tasks.downloads_pool()

        self.server = MusicServer(
            library=self.library,
            config=self.preferences.server_config(),
            control=self._remote_control,
            now_playing=self._now_playing,
        )

        #: The import being worked through, if any.
        self.import_job: Optional[imports.ImportJob] = None
        self.last_import_link = ""

        self._import_save = QTimer(self)
        self._import_save.setSingleShot(True)
        self._import_save.setInterval(1500)
        self._import_save.timeout.connect(
            lambda: self.import_job is not None and self.import_job.save()
        )

        #: Library writes are coalesced: an import finishing four hundred
        #: downloads should write the library once, not four hundred times.
        self._library_save = QTimer(self)
        self._library_save.setSingleShot(True)
        self._library_save.setInterval(2000)
        self._library_save.timeout.connect(self.library.save)

        self.setWindowTitle(APP_NAME)
        self.resize(*self.preferences.window_size)

        self._build()
        self._shortcuts()
        self._connect_playback()
        self.apply_appearance(self.appearance)
        self.show_section(self.preferences.section)

        self.playback.set_volume(self.preferences.volume)
        self._restore_session()

        QTimer.singleShot(900, self.check_unfinished_imports)

        if self.preferences.first_run:
            # Ask before the empty library is on screen — an empty list with no
            # explanation is the worst possible first impression.
            QTimer.singleShot(150, self.ask_for_music_folder)
        elif self.preferences.scan_on_start:
            QTimer.singleShot(400, self.rescan)
        if self.server.config.enabled:
            QTimer.singleShot(200, lambda: self.toggle_server(True))

    # ── Layout ────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.banner = Banner(self.appearance)
        layout.addWidget(self.banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._nav_rail())
        splitter.addWidget(self._sections())
        splitter.addWidget(self._queue_panel())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([self.preferences.sidebar_width, 780, 0])
        splitter.setChildrenCollapsible(True)
        self.splitter = splitter
        layout.addWidget(splitter, 1)

        layout.addWidget(self._player_bar())
        self.setCentralWidget(root)

    def _nav_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("Sidebar")
        rail.setMinimumWidth(150)
        rail.setMaximumWidth(260)

        layout = QVBoxLayout(rail)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(2)

        self.nav_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)

        for key, label, icon in SECTIONS:
            button = QPushButton(f"  {icon}   {label}")
            button.setObjectName("SidebarItem")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, key=key: self.show_section(key))
            group.addButton(button)
            layout.addWidget(button)
            self.nav_buttons[key] = button

        layout.addStretch(1)

        settings = QPushButton("  ⚙   Settings")
        settings.setObjectName("SidebarItem")
        settings.clicked.connect(self.open_settings)
        layout.addWidget(settings)

        return rail

    def _sections(self) -> QWidget:
        self.stack = QStackedWidget()
        self.views: dict[str, QWidget] = {}

        feed = FeedView(self.tastes, self.appearance)
        feed.refresh_requested.connect(self.rebuild_feed)
        feed.play_requested.connect(self.play_candidate)
        feed.download_requested.connect(self.download_candidate)
        feed.like_toggled.connect(self.toggle_like)
        feed.status.connect(self.notify)
        self._register("feed", feed)

        subscriptions = SubscriptionsView(self.tastes, self.appearance)
        subscriptions.subscribe_requested.connect(self.subscribe_to)
        subscriptions.unsubscribe.connect(self.unsubscribe_from)
        subscriptions.mute_toggled.connect(self.toggle_mute)
        subscriptions.open_channel.connect(self.open_channel)
        subscriptions.status.connect(self.notify)
        self._register("subscriptions", subscriptions)

        watch = WatchView(self.youtube, self.appearance)
        watch.playback_requested.connect(self.playback.pause)
        watch.download_requested.connect(self.download_video)
        watch.subscribe_requested.connect(self.subscribe_to_video_channel)
        watch.like_toggled.connect(self.like_video)
        watch.status.connect(self.notify)
        self._register("watch", watch)

        library_view = LibraryView(self.library, self.appearance)
        library_view.play_requested.connect(self.play_track)
        library_view.menu_requested.connect(self.open_track_menu)
        library_view.scan_requested.connect(self.rescan)
        self._register("library", library_view)

        albums = AlbumsView(self.library, self.appearance)
        albums.play_requested.connect(self.play_track)
        albums.menu_requested.connect(self.open_track_menu)
        self._register("albums", albums)

        playlists = PlaylistsView(self.library, self.playlists, self.appearance)
        playlists.play_requested.connect(self.play_track)
        playlists.menu_requested.connect(self.open_track_menu)
        self._register("playlists", playlists)

        youtube = YouTubeView(self.ytmusic, self.appearance)
        youtube.download_requested.connect(self.download_result)
        youtube.status.connect(self.notify)
        self._register("youtube", youtube)

        importer = ImportView(self.appearance)
        importer.import_requested.connect(self.import_spotify)
        importer.resume_requested.connect(self.resume_import)
        importer.takeout_requested.connect(self.import_takeout)
        importer.status.connect(self.notify)
        self._register("import", importer)

        downloads = DownloadsView(self.appearance)
        downloads.retry_requested.connect(self.download_result)
        self._register("downloads", downloads)

        server_view = ServerView(self.server, self.appearance)
        server_view.toggled.connect(self.toggle_server)
        server_view.status.connect(self.notify)
        self._register("server", server_view)

        return self.stack

    def _register(self, key: str, view: QWidget) -> None:
        self.views[key] = view
        self.stack.addWidget(view)

    def _queue_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("DetailPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setMinimumWidth(0)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Up next")
        title.setObjectName("Heading")
        header.addWidget(title)
        header.addStretch(1)

        clear = QPushButton("Clear")
        clear.setObjectName("Quiet")
        clear.clicked.connect(self._clear_queue)
        header.addWidget(clear)
        layout.addLayout(header)

        self.queue_body = QVBoxLayout()
        self.queue_body.setSpacing(2)
        layout.addLayout(self.queue_body)
        layout.addStretch(1)

        self.queue_panel = panel
        return panel

    def _player_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(92)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(14)

        # ── What is playing ──────────────────────────────────────
        self.now_art = CoverArt(60, self.appearance)
        self.now_art.set_track(None)
        layout.addWidget(self.now_art)

        column = QVBoxLayout()
        column.setSpacing(1)
        self.now_title = QLabel("Nothing playing")
        self.now_title.setStyleSheet("font-weight: 600; background: transparent;")
        column.addWidget(self.now_title)
        self.now_artist = QLabel("")
        self.now_artist.setObjectName("Subtle")
        column.addWidget(self.now_artist)
        layout.addLayout(column)
        layout.addSpacing(6)

        # ── Transport ────────────────────────────────────────────
        for text, slot, name, tip in (
            ("⇄", self.toggle_shuffle, "shuffle", "Shuffle  (Ctrl+H)"),
            ("⏮", self.playback.previous, "previous", "Previous  (Ctrl+Left)"),
            ("⏵", self.playback.toggle, "play", "Play or pause  (Space)"),
            ("⏭", self.playback.next, "next", "Next  (Ctrl+Right)"),
            ("↻", self.cycle_repeat, "repeat", "Repeat  (Ctrl+R)"),
        ):
            button = QPushButton(text)
            button.setObjectName("Primary" if name == "play" else "Quiet")
            button.setFixedWidth(46 if name == "play" else 36)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            setattr(self, f"{name}_button", button)
            layout.addWidget(button)

        # ── Seek ─────────────────────────────────────────────────
        seek_column = QVBoxLayout()
        seek_column.setSpacing(2)

        self.visualizer = Visualizer(
            self.appearance,
            shape=self.preferences.shape(),
            height=30,
            blur=self.preferences.visualizer_blur,
            alpha=self.preferences.visualizer_alpha / 100,
        )
        self.visualizer.setVisible(self.preferences.visualizer)
        seek_column.addWidget(self.visualizer)

        seek_row = QHBoxLayout()
        seek_row.setSpacing(8)

        self.elapsed_label = QLabel("0:00")
        self.elapsed_label.setObjectName("Subtle")
        seek_row.addWidget(self.elapsed_label)

        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderReleased.connect(self._seek_released)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        seek_row.addWidget(self.seek, 1)

        self.total_label = QLabel("0:00")
        self.total_label.setObjectName("Subtle")
        seek_row.addWidget(self.total_label)

        seek_column.addLayout(seek_row)
        layout.addLayout(seek_column, 1)
        self._seeking = False

        # ── Volume and queue ─────────────────────────────────────
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setFixedWidth(90)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(self.preferences.volume * 100))
        self.volume.valueChanged.connect(lambda value: self.playback.set_volume(value / 100))
        layout.addWidget(self.volume)

        queue_button = QPushButton("≡")
        queue_button.setObjectName("Quiet")
        queue_button.setToolTip("Show the queue  (Ctrl+Q)")
        queue_button.clicked.connect(self.toggle_queue)
        layout.addWidget(queue_button)

        return bar

    def _shortcuts(self) -> None:
        bindings = [
            ("Space", self.playback.toggle),
            ("Ctrl+Right", self.playback.next),
            ("Ctrl+Left", self.playback.previous),
            ("Ctrl+H", self.toggle_shuffle),
            ("Ctrl+R", self.cycle_repeat),
            ("Ctrl+Q", self.toggle_queue),
            ("Ctrl+F", self._focus_search),
            ("Ctrl+,", self.open_settings),
            ("Ctrl+Shift+R", self.rescan),
        ]
        for index, (key, _label, _icon) in enumerate(SECTIONS):
            bindings.append((f"Ctrl+{index + 1}", lambda key=key: self.show_section(key)))

        for keys, slot in bindings:
            action = QAction(self)
            action.setShortcut(QKeySequence(keys))
            action.triggered.connect(slot)
            self.addAction(action)

    def _connect_playback(self) -> None:
        self.playback.track_changed.connect(self._on_track_changed)
        self.playback.state_changed.connect(self._on_state_changed)
        self.playback.position_changed.connect(self._on_position)
        self.playback.queue_changed.connect(self._refresh_queue)
        self.playback.finished.connect(lambda: self.notify("Queue finished", "info"))

    # ── Sections ──────────────────────────────────────────────────

    def show_section(self, key: str) -> None:
        view = self.views.get(key)
        if view is None:
            key, view = "library", self.views["library"]

        self.stack.setCurrentWidget(view)
        self.preferences.section = key

        button = self.nav_buttons.get(key)
        if button is not None:
            button.setChecked(True)

        self.refresh()

    def refresh(self) -> None:
        current = self.stack.currentWidget()
        if hasattr(current, "refresh"):
            playing = self.playback.track.path if self.playback.track else ""
            try:
                current.refresh(playing)
            except TypeError:
                current.refresh()

    # ── Playing ───────────────────────────────────────────────────

    def play_track(self, track: Track, context: Optional[list[Track]] = None) -> None:
        """Play a track in the context of the list it was clicked in."""
        # Music and a video playing over each other is nobody's intent.
        watch = self.views.get("watch")
        if watch is not None:
            watch.stop()

        tracks = list(context) if context else [track]
        try:
            start = next(i for i, t in enumerate(tracks) if t.path == track.path)
        except StopIteration:
            start = 0
        self.playback.play_tracks(tracks, start)

    def toggle_shuffle(self) -> None:
        state = self.playback.toggle_shuffle()
        self.notify("Shuffle on" if state else "Shuffle off", "info")
        self._update_mode_buttons()

    def cycle_repeat(self) -> None:
        mode = self.playback.cycle_repeat()
        self.notify(mode.label, "info")
        self._update_mode_buttons()

    def _update_mode_buttons(self) -> None:
        theme = self.appearance.theme
        on = f"color: {theme.accent}; background: transparent; border: none;"
        off = f"color: {theme.text_dim}; background: transparent; border: none;"

        self.shuffle_button.setStyleSheet(on if self.playback.queue.shuffle else off)
        repeat = self.playback.queue.repeat
        self.repeat_button.setStyleSheet(off if repeat is Repeat.OFF else on)
        self.repeat_button.setText("↻¹" if repeat is Repeat.ONE else "↻")

    def _note_local_play(self, track: Optional[Track]) -> None:
        """Local listening shapes the feed too — it is the same signal."""
        if track is None:
            return
        self.tastes.note_play(
            track.source_id or track.path, track.display_title,
            track.display_artist, completion=1.0,
        )
        self.tastes.save()

    def _on_track_changed(self, track: Optional[Track]) -> None:
        self.now_art.set_track(track)
        self.now_title.setText(track.display_title if track else "Nothing playing")
        self.now_artist.setText(track.display_artist if track else "")
        self.setWindowTitle(
            f"{track.display_title} — {track.display_artist} · {APP_NAME}"
            if track else APP_NAME
        )
        self._refresh_queue()
        self.refresh()

    def _on_state_changed(self, playing: bool) -> None:
        self.play_button.setText("⏸" if playing else "⏵")
        self.visualizer.set_live(playing)

    def _on_position(self, position: int, duration: int) -> None:
        if not self._seeking:
            self.seek.setValue(int(position / duration * 1000) if duration else 0)
        self.elapsed_label.setText(_clock(position))
        self.total_label.setText(_clock(duration))

    def _seek_released(self) -> None:
        self._seeking = False
        duration = self.playback.duration
        if duration:
            self.playback.seek(int(self.seek.value() / 1000 * duration))

    # ── The queue ─────────────────────────────────────────────────

    def toggle_queue(self) -> None:
        sizes = self.splitter.sizes()
        showing = sizes[2] > 0
        self.splitter.setSizes([sizes[0], sizes[1], 0 if showing else 300])
        if not showing:
            self._refresh_queue()

    def _refresh_queue(self) -> None:
        while self.queue_body.count():
            item = self.queue_body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        upcoming = self.playback.queue.upcoming[:40]
        if not upcoming:
            empty = QLabel("Nothing queued")
            empty.setObjectName("Subtle")
            self.queue_body.addWidget(empty)
            return

        for index, track in enumerate(upcoming):
            row = QPushButton(f"{track.display_title}\n{track.display_artist}")
            row.setObjectName("SidebarItem")
            row.clicked.connect(
                lambda _c=False, index=index: self.playback.jump_to(
                    self.playback.queue.position + 1 + index)
            )
            self.queue_body.addWidget(row)

    def _clear_queue(self) -> None:
        self.playback.queue.clear()
        self.playback.stop()
        self._refresh_queue()

    def open_track_menu(self, track: Track, position) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(self.appearance.stylesheet())

        play_now = menu.addAction("Play now")
        play_next = menu.addAction("Play next")
        queue = menu.addAction("Add to queue")
        menu.addSeparator()

        playlist_menu = menu.addMenu("Add to playlist")
        playlist_actions = {}
        for playlist in self.playlists.all(self.library):
            playlist_actions[playlist_menu.addAction(playlist.title)] = playlist
        if not playlist_actions:
            playlist_menu.setEnabled(False)

        menu.addSeparator()
        reveal = menu.addAction("Show the file")

        chosen = menu.exec(position)
        if chosen is None:
            return

        if chosen is play_now:
            self.play_track(track)
        elif chosen is play_next:
            self.playback.play_next([track])
        elif chosen is queue:
            self.playback.enqueue([track])
        elif chosen in playlist_actions:
            playlist = playlist_actions[chosen]
            playlist.add([track])
            self.playlists.save(playlist)
            self.notify(f"Added to {playlist.title}", "success")
        elif chosen is reveal:
            self._reveal(Path(track.path))

    def _reveal(self, path: Path) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # ── Library ───────────────────────────────────────────────────

    def ask_for_music_folder(self) -> None:
        """First launch: confirm where the music is, then scan it."""
        dialog = FirstRunDialog(self.appearance, self)

        if dialog.exec() != FirstRunDialog.DialogCode.Accepted:
            # Dismissing is a real answer, and it is remembered: saving the
            # preferences file is what stops this being asked again.
            self.preferences.save()
            self.notify("You can add a music folder any time in Settings", "info")
            return

        folder = str(dialog.folder)
        self.preferences.folders = [folder]
        self.preferences.scan_on_start = dialog.scan_on_start
        self.preferences.save()

        self.library.folders = [folder]
        self.rescan()

    def rescan(self) -> None:
        self.notify("Scanning for music…", "info")
        tasks.run(
            self.library.rescan,
            on_done=self._scanned,
            on_error=lambda message: self.notify(f"Scan failed: {message}", "error"),
        )

    def _scanned(self, result) -> None:
        added, removed = result if isinstance(result, tuple) else (0, 0)
        self.library.save()

        if added or removed:
            parts = []
            if added:
                parts.append(f"{added} added")
            if removed:
                parts.append(f"{removed} gone")
            self.notify(f"Library updated — {', '.join(parts)}", "success")
        elif not self.library.tracks:
            self.notify("No music found. Add a folder in Settings.", "warning")

        self.refresh()

    # ── Downloading ───────────────────────────────────────────────

    def download_result(self, result: ytmusic.Result) -> None:
        request = ytmusic.DownloadRequest(
            video_id=result.id, title=result.title,
            artist=result.artist, album=result.album,
            fmt=self.preferences.download_format,
        )
        self._download(request)

    def _download(self, request: ytmusic.DownloadRequest) -> None:
        downloads = self.views["downloads"]
        key = request.video_id

        # Asking for the same track twice while the first is still going costs
        # bandwidth and produces two files racing for the same name. Pressing
        # Download twice is a normal thing to do, so it is handled here rather
        # than treated as user error.
        existing = downloads.entries.get(key)
        if existing is not None and existing[2] in ("queued", "downloading", "converting"):
            self.notify(f"Already downloading {request.title}", "info")
            return

        # Already in the library, from a previous run or a previous import.
        if any(t.source_id == key for t in self.library.tracks.values()):
            self.notify(f"{request.title} is already in your library", "info")
            return
        label = f"{request.artist} — {request.title}" if request.artist else request.title
        downloads.note(key, label, 0.0, "queued", request)

        folder = self.preferences.downloads_path()
        cookies = self.preferences.cookies()

        def work(report) -> ytmusic.DownloadResult:
            return ytmusic.download(
                request, folder,
                progress=lambda fraction, state: report((fraction, state)),
                cookies=cookies,
            )

        tasks.run(
            work,
            on_progress=lambda update, key=key, label=label: downloads.note(
                key, label, update[0], update[1], request),
            on_done=lambda outcome, key=key, label=label: self._downloaded(outcome, key, label),
            on_error=lambda message, key=key, label=label: downloads.note(
                key, label, 0.0, "failed", request),
            pool=self.downloads_pool,
        )

    def _downloaded(self, outcome: ytmusic.DownloadResult, key: str, label: str) -> None:
        downloads = self.views["downloads"]

        if not outcome.ok:
            downloads.note(key, label, 0.0, "failed", outcome.request)
            if self.import_job is not None:
                self.import_job.note_failed(key, outcome.error)
                self._import_save.start()
            self.notify(f"Download failed: {outcome.error[:80]}", "error")
            return

        downloads.note(key, label, 1.0, "done", None)

        if self.import_job is not None:
            self.import_job.note_done(key, outcome.path)
            self._import_save.start()

        if self.preferences.add_downloads_to_library:
            track = ytmusic.track_from_download(outcome)
            if track is not None:
                self.library.add(track)
                self._library_save.start()
                # Only redraw if the library is what is on screen; during an
                # import the downloads view is, and it repaints itself.
                if self.stack.currentWidget() in (self.views["library"], self.views["albums"]):
                    self.refresh()

        self.notify(f"Downloaded {label}", "success")

    # ── The local algorithm ───────────────────────────────────────

    def rebuild_feed(self) -> None:
        """Gather candidates, then rank them here.

        The gathering needs the network; the ranking does not, and never leaves
        this machine. That split is the whole design.
        """
        view = self.views["feed"]
        channels = self.tastes.subscriptions()

        if not channels and not self.tastes.signals:
            self.notify("Follow something or play some music first", "warning")
            return

        def work(report) -> list:
            candidates: list[Candidate] = []

            if channels:
                candidates.extend(yt.subscription_candidates(
                    self.youtube, channels, report=report))

            # Related tracks to what you liked or replayed, as further
            # candidates — YouTube's suggestions, ranked by our weights.
            from rose_bouquet.core.recommend import seeds

            for seed in seeds(self.tastes, limit=4):
                report(f"Looking for more like {seed.title or seed.id}")
                for video in self.youtube.related(seed.id, limit=8):
                    candidates.append(video.to_candidate("related"))

            report(f"Ranking {len(candidates)} candidates")
            return rank(candidates, self.tastes, limit=60)

        view.show_progress("Checking your subscriptions…")
        tasks.run(
            work,
            on_progress=view.show_progress,
            on_done=lambda ranked: (view.show_feed(ranked),
                                    self.notify(f"{len(ranked)} things for you", "success")),
            on_error=lambda message: (view.show_feed([]),
                                      self.notify(f"Could not build the feed: {message}", "error")),
        )

    def play_candidate(self, item: Candidate) -> None:
        """Stream something from the feed without downloading it first."""
        self.notify(f"Loading {item.title}…", "info")

        def work():
            return self.youtube.stream_url(item.id, audio_only=True)

        def play(url: str) -> None:
            if not url:
                self.notify("Could not get a stream for that", "error")
                return

            track = Track(
                path=url, title=item.title, artist=item.artist,
                album="YouTube", duration=item.duration,
                source="youtube", source_id=item.id,
            )
            self.playback.play_tracks([track], 0)
            self.tastes.note_play(item.id, item.title, item.artist, item.channel_id)
            self.tastes.save()

        tasks.run(work, on_done=play,
                  on_error=lambda message: self.notify(f"Stream failed: {message}", "error"))

    def download_candidate(self, item: Candidate) -> None:
        self._download(ytmusic.DownloadRequest(
            video_id=item.id, title=item.title, artist=item.artist,
            fmt=self.preferences.download_format,
        ))

    def download_video(self, video) -> None:
        """Take the audio from a video being watched or listed."""
        self._download(ytmusic.DownloadRequest(
            video_id=video.id, title=video.title, artist=video.channel,
            fmt=self.preferences.download_format,
        ))

    def subscribe_to_video_channel(self, video) -> None:
        channel = video.to_channel()
        if not channel.id and not channel.title:
            self.notify("That video does not say which channel it is from", "warning")
            return

        followed = self.tastes.toggle_subscription(channel)
        self.tastes.save()
        self.notify(
            f"Following {channel.title}" if followed else f"Unfollowed {channel.title}",
            "success" if followed else "info",
        )
        self.refresh()

    def like_video(self, video) -> None:
        liked = self.tastes.like(video.id, video.title, video.channel, video.channel_id)
        self.tastes.save()
        self.notify("Liked" if liked else "Like removed", "success" if liked else "info")

    def watch_video(self, video) -> None:
        """Open something in the watch screen from anywhere else in the app."""
        self.show_section("watch")
        self.views["watch"].watch(video)

    def toggle_like(self, item: Candidate) -> None:
        liked = self.tastes.like(item.id, item.title, item.artist, item.channel_id)
        self.tastes.save()
        self.notify("Liked — your feed will lean this way" if liked else "Like removed",
                    "success" if liked else "info")
        self.refresh()

    def subscribe_to(self, link: str) -> None:
        def work():
            return self.youtube.channel(link)

        def followed(channel) -> None:
            if channel is None:
                self.notify("Could not find that channel", "error")
                return
            self.tastes.subscribe(channel)
            self.tastes.save()
            self.notify(f"Following {channel.title}", "success")
            self.refresh()

        self.notify("Looking that up…", "info")
        tasks.run(work, on_done=followed,
                  on_error=lambda message: self.notify(f"Lookup failed: {message}", "error"))

    def unsubscribe_from(self, channel_id: str) -> None:
        self.tastes.unsubscribe(channel_id)
        self.tastes.save()
        self.refresh()

    def toggle_mute(self, channel_id: str) -> None:
        channel = self.tastes.channels.get(channel_id)
        if channel is not None:
            channel.muted = not channel.muted
            self.tastes.save()
            self.refresh()

    def open_channel(self, channel: Channel) -> None:
        """Show a channel's recent uploads, watchable."""
        watch = self.views["watch"]
        watch.loading = True
        watch.refresh()
        self.show_section("watch")

        def work():
            return self.youtube.uploads(channel.id or channel.title, limit=30)

        tasks.run(
            work,
            on_done=lambda videos: watch.show_videos(
                videos, note=f"{len(videos)} from {channel.title}" if videos else "Nothing there"),
            on_error=lambda message: (watch.show_videos([]),
                                      self.notify(f"Could not load that: {message}", "error")),
        )

    # ── Spotify import ────────────────────────────────────────────

    def import_spotify(self, link: str, text: str, download: bool,
                       start_offset: int = 0, job=None) -> None:
        """Read a playlist — all of it — matching as it goes.

        Long playlists come back a hundred at a time, and Spotify will cut the
        connection off if asked too often. Both are handled the same way: read
        what we can, match it, write down where we stopped, and carry on from
        there — either straight away or the next time the user presses the
        button.
        """
        importer = self.views["import"]
        self.last_import_link = link.strip()
        credentials = _credentials()

        def work(report) -> tuple:
            report("Reading the playlist…")

            title, tracks, next_offset, total, problem, wait = "", [], None, 0, "", 0

            if link:
                page = spotify.read_all(
                    link,
                    client_id=credentials.get("spotify_client_id", ""),
                    client_secret=credentials.get("spotify_client_secret", ""),
                    start_offset=start_offset,
                    report=report,
                )
                tracks, next_offset, total = page.tracks, page.next_offset, page.total
                problem = page.error
                wait = page.retry_after

                if not tracks and not problem:
                    # Nothing from the API route; the embed still gives the
                    # first hundred without any credentials at all.
                    title, tracks = spotify.from_embed(link)
                    next_offset = None if len(tracks) < spotify.EMBED_LIMIT else len(tracks)

            if not tracks and text:
                tracks = spotify.from_text(text)
                next_offset = None

            if not tracks:
                return "", spotify.ImportReport(), next_offset, total, problem, wait

            report(f"Matching {len(tracks)} tracks on YouTube Music")

            def progress(index: int, count: int, track) -> None:
                report(f"Matching {index} of {count} — {track}")

            found = spotify.match_all(tracks, self.ytmusic.best_match, progress=progress)
            found.title = title or (job.title if job else "") or "Imported playlist"
            return title, found, next_offset, total, problem, wait

        importer.show_progress("Reading the playlist…")
        tasks.run(
            work,
            on_progress=importer.show_progress,
            on_done=lambda outcome: self._imported(outcome, download, job=job),
            on_error=lambda message: (
                importer.show_report(None), self.notify(f"Import failed: {message}", "error")
            ),
        )

    def _imported(self, outcome, download: bool, job=None) -> None:
        _title, report, next_offset, expected, problem, wait = outcome
        importer = self.views["import"]
        importer.show_report(report)

        if not report.total:
            self.notify(problem or "Nothing could be read from that playlist", "warning")
            return

        # An import is a record on disk, so an interrupted one can be picked up
        # where it stopped instead of starting over.
        job = job or imports.ImportJob.for_link(self.last_import_link, report.title)
        job.next_offset = next_offset
        job.expected_total = expected or job.expected_total
        job.partial = next_offset is not None
        if wait:
            job.block_for(wait)
        job.add_tracks([source for source, _found in report.matched] + report.missed)

        for source, found in report.matched:
            entry = next((e for e in job.entries if e.key == _entry_key(source)), None)
            if entry is not None:
                job.note_match(entry, getattr(found, "id", ""))
        for source in report.missed:
            entry = next((e for e in job.entries if e.key == _entry_key(source)), None)
            if entry is not None:
                entry.state = imports.MISSING

        already = job.skip_already_downloaded(self.library)
        job.save()
        self.import_job = job

        if already:
            self.notify(f"{already} of these are already in your library", "info")

        # The playlist is created either way, so the misses are recorded even if
        # nothing is downloaded.
        playlist = self.playlists.create(report.title or "Imported playlist")
        playlist.source = "spotify"
        playlist.missing = report.missed_lines()
        self.playlists.save(playlist)

        if next_offset is not None:
            # Cut short. Say so, and keep the place.
            self.notify(
                f"Read {job.total} so far — {problem or 'more to come'}. "
                "Press Carry on to continue from where it stopped.", "warning",
            )
            self.views["import"].show_unfinished([job])
        else:
            self.notify(report.summary, "success" if not report.missed else "warning")

        if download:
            self.download_pending(job)

    def download_pending(self, job=None) -> None:
        """Download everything an import still owes, and remember what lands.

        Called both after an import and to resume one: the record knows which
        rows are outstanding, so being interrupted costs nothing but the
        download that was in flight.
        """
        job = job or self.import_job
        if job is None:
            return

        outstanding = job.pending()
        if not outstanding:
            self.notify(job.summary, "success")
            return

        self.notify(f"Downloading {len(outstanding)} tracks — {job.summary}", "info")
        for entry in outstanding:
            self._download(ytmusic.DownloadRequest(
                video_id=entry.video_id, title=entry.title,
                artist=entry.artist, fmt=self.preferences.download_format,
            ))
        self.show_section("downloads")

    def import_takeout(self, path: str) -> None:
        """Fold a Google Takeout export into the local profile."""
        if not path:
            self.notify("Choose your Takeout zip or folder first", "warning")
            return

        self.notify("Reading your export…", "info")

        def work():
            data = takeout.read(Path(path))
            return data, takeout.apply(data, self.tastes)

        def done(outcome) -> None:
            _data, (plays, followed) = outcome
            if not plays and not followed:
                self.notify(
                    "Nothing usable in there — it needs watch-history.json or "
                    "subscriptions.csv from a YouTube Takeout", "warning")
                return

            self.tastes.save()
            self.notify(
                f"Imported {plays} watched videos and {followed} subscriptions "
                "— rebuild the feed to use them", "success")
            self.refresh()

        tasks.run(work, on_done=done,
                  on_error=lambda message: self.notify(f"Could not read that: {message}", "error"))

    def resume_import(self, job) -> None:
        """Pick up an import that was cut short — reading as well as downloading."""
        skipped = job.skip_already_downloaded(self.library)
        job.save()
        self.import_job = job

        if skipped:
            self.notify(f"{skipped} were already in your library", "info")

        waiting = job.wait_remaining()
        if waiting and not job.fully_read:
            # Asking again inside the window just makes the block longer.
            self.notify(
                f"Spotify will not answer for another {imports._plainly(waiting)}. "
                f"The {job.total} already matched can still download.", "warning")
            self.download_pending(job)
            return

        if not job.fully_read and job.link:
            # There is more playlist to read. Carry on from the exact offset,
            # then download everything outstanding.
            self.notify(f"Carrying on from track {job.next_offset}…", "info")
            self.import_spotify(job.link, "", True, start_offset=job.next_offset, job=job)
            return

        self.download_pending(job)

    def check_unfinished_imports(self) -> None:
        """Look for interrupted imports and offer them on the import screen."""
        jobs = imports.unfinished()
        self.views["import"].show_unfinished(jobs)
        if jobs:
            self.notify(
                f"“{jobs[0].title}” was left unfinished — {jobs[0].summary}", "warning"
            )

    # ── Server ────────────────────────────────────────────────────

    def toggle_server(self, on: bool) -> None:
        if on:
            ok, message = self.server.start()
            self.notify(message, "success" if ok else "error")
        else:
            self.server.stop()
            self.notify("Stopped serving", "info")

        self.server.config.enabled = on
        self.preferences.set_server_config(self.server.config)
        self.preferences.save()

        if self.stack.currentWidget() is self.views["server"]:
            self.views["server"].refresh()

    def _remote_control(self, action: str, value: str):
        """Let a client on the network drive this player.

        Only reachable when the user has turned remote control on, and only
        the transport — nothing here can delete a file or read the disk.
        """
        if not self.preferences.remote_control:
            return {"error": "remote control is off"}

        actions = {
            "play": self.playback.play,
            "pause": self.playback.pause,
            "toggle": self.playback.toggle,
            "next": self.playback.next,
            "previous": self.playback.previous,
        }
        handler = actions.get(action)
        if handler is None:
            return {"error": f"unknown action {action}"}

        # Qt objects must be touched on the interface thread, and this arrives
        # on an HTTP worker.
        QTimer.singleShot(0, handler)
        return {"ok": True}

    def _now_playing(self) -> dict:
        track = self.playback.track
        if track is None:
            return {"playing": False}
        return {
            "playing": self.playback.playing,
            "title": track.display_title,
            "artist": track.display_artist,
            "album": track.display_album,
            "position": self.playback.position,
            "duration": self.playback.duration,
            "at": datetime.now().isoformat(timespec="seconds"),
        }

    # ── Chrome ────────────────────────────────────────────────────

    def notify(self, message: str, kind: str = "info") -> None:
        self.banner.show_message(message, kind=kind)

    def _focus_search(self) -> None:
        current = self.stack.currentWidget()
        search = getattr(current, "search", None)
        if search is not None:
            search.setFocus()
            search.selectAll()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.preferences, self)
        dialog.appearance_changed.connect(self.apply_appearance)
        dialog.library_changed.connect(self._library_folders_changed)
        dialog.visualizer_changed.connect(self._visualizer_changed)
        dialog.server_changed.connect(self._server_settings_changed)
        dialog.exec()

    def _library_folders_changed(self) -> None:
        self.library.folders = list(self.preferences.folders)
        self.rescan()

    def _visualizer_changed(self) -> None:
        self.visualizer.setVisible(self.preferences.visualizer)
        self.visualizer.set_shape(self.preferences.shape())
        self.visualizer.alpha = self.preferences.visualizer_alpha / 100
        if self.preferences.visualizer:
            self.visualizer.start()
        else:
            self.visualizer.stop()

    def _server_settings_changed(self) -> None:
        self.server.config = self.preferences.server_config()
        if self.server.running:
            ok, message = self.server.restart()
            self.notify(message, "success" if ok else "error")

    def apply_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance
        set_active_style(appearance.style)
        self.setStyleSheet(appearance.stylesheet())

        for widget in (*self.views.values(), self.banner, self.visualizer, self.now_art):
            stage = getattr(widget, "stage_appearance", None)
            if stage is not None:
                stage(appearance)
            elif hasattr(widget, "apply_appearance"):
                widget.apply_appearance(appearance)

        self._update_mode_buttons()
        self.refresh()

    # ── Session ───────────────────────────────────────────────────

    def _session_path(self) -> Path:
        return data_dir() / "session.json"

    def _restore_session(self) -> None:
        import json

        try:
            state = json.loads(self._session_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        self.playback.restore_state(state)
        self._refresh_queue()

        if self.preferences.visualizer:
            self.visualizer.start()

    def _save_session(self) -> None:
        import json

        try:
            path = self._session_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.playback.save_state()), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not save the session: %s", exc)

    def closeEvent(self, event) -> None:
        # Anything still running is about to lose the widgets it reports to.
        tasks.cancel_all()
        self._save_session()
        self.tastes.save()
        if self.import_job is not None:
            self.import_job.save()
        self.views["watch"].stop()
        self.playback.stop()
        self.visualizer.stop()
        self.server.stop()

        self.preferences.window_size = (self.width(), self.height())
        sizes = self.splitter.sizes()
        if sizes:
            self.preferences.sidebar_width = sizes[0]
        self.preferences.volume = self.playback.volume
        self.preferences.save()

        self.library.save()
        super().closeEvent(event)


def _entry_key(track) -> str:
    """The same identity `imports.Entry` uses, for lining rows up."""
    artist = getattr(track, "artist", "") or ""
    title = getattr(track, "title", "") or ""
    return f"{artist.lower().strip()}|{title.lower().strip()}"


def _clock(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _credentials() -> dict:
    from rose_bouquet.ui.preferences import load_credentials

    return load_credentials()
