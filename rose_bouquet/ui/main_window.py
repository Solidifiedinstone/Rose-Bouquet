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

from rose_bouquet.core import spotify, ytmusic
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
from rose_bouquet.ui.widgets import Banner, CoverArt

logger = logging.getLogger(__name__)

SECTIONS = [
    ("feed", "For you", "✦"),
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

        self.setWindowTitle(APP_NAME)
        self.resize(*self.preferences.window_size)

        self._build()
        self._shortcuts()
        self._connect_playback()
        self.apply_appearance(self.appearance)
        self.show_section(self.preferences.section)

        self.playback.set_volume(self.preferences.volume)
        self._restore_session()

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
        label = f"{request.artist} — {request.title}" if request.artist else request.title
        downloads.note(key, label, 0.0, "queued", request)

        folder = self.preferences.downloads_path()

        def work(report) -> ytmusic.DownloadResult:
            return ytmusic.download(
                request, folder,
                progress=lambda fraction, state: report((fraction, state)),
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
            self.notify(f"Download failed: {outcome.error[:80]}", "error")
            return

        downloads.note(key, label, 1.0, "done", None)

        if self.preferences.add_downloads_to_library:
            track = ytmusic.track_from_download(outcome)
            if track is not None:
                self.library.add(track)
                self.library.save()
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
        """Show a channel's recent uploads in the feed view."""
        view = self.views["feed"]
        view.show_progress(f"Loading {channel.title}…")
        self.show_section("feed")

        def work():
            videos = self.youtube.uploads(channel.id or channel.title, limit=30)
            return rank([v.to_candidate("channel") for v in videos], self.tastes, limit=30)

        tasks.run(work, on_done=view.show_feed,
                  on_error=lambda message: (view.show_feed([]),
                                            self.notify(f"Could not load that: {message}", "error")))

    # ── Spotify import ────────────────────────────────────────────

    def import_spotify(self, link: str, text: str, download: bool) -> None:
        importer = self.views["import"]
        credentials = _credentials()

        def work(report) -> tuple[str, spotify.ImportReport]:
            report("Reading the playlist…")

            title, tracks = "", []
            if link:
                title, tracks = spotify.fetch_playlist(
                    link,
                    credentials.get("spotify_client_id", ""),
                    credentials.get("spotify_client_secret", ""),
                )
            if not tracks and text:
                tracks = spotify.from_text(text)

            if tracks:
                report(f"Read {len(tracks)} tracks")

            if not tracks:
                return "", spotify.ImportReport()

            def progress(index: int, total: int, track) -> None:
                report(f"Matching {index + 1} of {total} — {track}")

            found = spotify.match_all(
                tracks, self.ytmusic.best_match, progress=progress
            )
            found.title = title or "Imported playlist"
            # An import that stopped at exactly one page probably did not read
            # the whole playlist, and saying so beats silently importing 100 of
            # 400 tracks.
            found.truncated = spotify.looks_truncated(tracks)
            return title, found

        importer.show_progress("Reading the playlist…")
        tasks.run(
            work,
            on_progress=importer.show_progress,
            on_done=lambda outcome: self._imported(outcome, download),
            on_error=lambda message: (
                importer.show_report(None), self.notify(f"Import failed: {message}", "error")
            ),
        )

    def _imported(self, outcome, download: bool) -> None:
        _title, report = outcome
        importer = self.views["import"]
        importer.show_report(report)

        if not report.total:
            self.notify("Nothing could be read from that playlist", "warning")
            return

        # The playlist is created either way, so the misses are recorded even if
        # nothing is downloaded.
        playlist = self.playlists.create(report.title or "Imported playlist")
        playlist.source = "spotify"
        playlist.missing = report.missed_lines()
        self.playlists.save(playlist)

        if getattr(report, "truncated", False):
            self.notify(
                f"Only the first {report.total} tracks could be read — Spotify's "
                "public endpoint pages 100 at a time. Add API credentials in "
                "Settings → Downloads to get the rest.", "warning",
            )
        else:
            self.notify(report.summary, "success" if not report.missed else "warning")

        if download:
            for _source, found in report.matched:
                self._download(ytmusic.DownloadRequest(
                    video_id=found.id, title=found.title,
                    artist=found.artist, album=found.album,
                    fmt=self.preferences.download_format,
                ))
            self.show_section("downloads")

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
        self._save_session()
        self.tastes.save()
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
