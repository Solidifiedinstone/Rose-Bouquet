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

from PySide6.QtCore import QEasingCurve, Qt, QTimer, QVariantAnimation
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

from rose_bouquet.core import cava, imports, optical, spotify, ytmusic
from rose_bouquet.core import youtube as yt
from rose_bouquet.core.library import Library, Track, data_dir
from rose_bouquet.core.media import Candidate
from rose_bouquet.core.mpris import Mpris
from rose_bouquet.core.playlists import PlaylistStore
from rose_bouquet.core.playqueue import Repeat
from rose_bouquet.core.server import MusicServer
from rose_bouquet.ui import tasks
from rose_bouquet.ui.branding import APP_NAME
from rose_bouquet.ui.cdplayer import CdPlayer
from rose_bouquet.ui.disc import DiscView
from rose_bouquet.ui.first_run import FirstRunDialog
from rose_bouquet.ui.playback import Playback
from rose_bouquet.ui.preferences import Preferences
from rose_bouquet.ui.settings import SettingsDialog
from rose_bouquet.ui.theme import Appearance, set_active_style
from rose_bouquet.ui.video import VideoStage
from rose_bouquet.ui.views import (
    AlbumsView,
    DownloadsView,
    ImportView,
    LibraryView,
    PlaylistsView,
    ServerView,
)
from rose_bouquet.ui.visualizer import FullscreenVisualizer, Shape, Visualizer
from rose_bouquet.ui.widgets import Banner, CoverArt, UpdateBar

logger = logging.getLogger(__name__)

SECTIONS = [
    ("watch", "YouTube", "▶"),
    ("library", "Library", "♫"),
    ("albums", "Albums", "▣"),
    ("playlists", "Playlists", "≡"),
    ("import", "Import", "⤓"),
    ("downloads", "Downloads", "↓"),
    ("disc", "Disc", "◎"),
    ("server", "Serve", "⇄"),
]

#: Width of the sidebar once it has been pulled in — wide enough for the
#: toggle and nothing else.
SIDEBAR_RAIL_WIDTH = 52
SIDEBAR_ANIMATION_MS = 200

#: How many results one watched thing may contribute to a feed. Without a cap
#: a single stray view — something watched once, out of character — becomes a
#: tenth of the screen, and the feed reads as random to the person who watched
#: it and forgot.
PER_SEED = 4

#: Asked of YouTube for the up-next column. More than are shown, because the
#: ranker drops the ones already watched and anything blocked, and a column
#: that empties itself out on somebody with a long history is worse than one
#: that had a few spare to work with.
#: How long between unasked-for update checks. A release is not urgent enough
#: to ask GitHub on every launch.
UPDATE_CHECK_INTERVAL = 24 * 60 * 60

RELATED_CANDIDATES = 24

#: How many end up beside the picture. Enough to choose from, few enough that
#: the choice is still a choice.
RELATED_SHOWN = 10

#: Below this the window is too narrow to give a third of itself to a sidebar,
#: so the rail pulls itself in. Phones are the obvious case, but a half-screen
#: window on a laptop hits it too — and the user's own choice is remembered
#: separately, so widening the window gives them back what they had.
NARROW_WIDTH = 720


class Sections(dict):
    """The stack's views, some of which are not built until first opened.

    Most sections are a handful of labels and cost nothing to make up front.
    The YouTube tab is a whole Chromium — a profile, a render process and a
    page load — and the video stage is a second media pipeline, and someone
    who opened the app to play an album pays for both of those at every
    launch and then leaves them running in the background all evening.

    Registering a factory instead of a widget defers that to the first time
    the section is actually asked for. Everything that reaches into
    `views[key]` gets a built widget either way, so nothing else has to know
    which sections are lazy; the one deliberate difference is `values()`,
    which yields only what exists — restyling a section nobody has opened
    would build it, which is the cost we are avoiding.
    """

    def __init__(self, adopt) -> None:
        super().__init__()
        #: Called with each newly built view, to put it in the stack.
        self._adopt = adopt
        self._factories: dict = {}

    def add(self, key: str, view) -> None:
        self[key] = view
        self._adopt(view)

    def add_lazy(self, key: str, factory) -> None:
        self._factories[key] = factory

    def build(self, key: str):
        """The view for `key`, made now if it has not been made yet."""
        view = super().get(key)
        if view is None:
            factory = self._factories.get(key)
            if factory is None:
                return None
            view = factory()
            self[key] = view
            self._adopt(view)
        return view

    def built(self, key: str):
        """The view for `key` only if it already exists — never builds one."""
        return super().get(key)

    def __missing__(self, key: str):
        view = self.build(key)
        if view is None:
            raise KeyError(key)
        return view

    def get(self, key, default=None):
        view = self.build(key)
        return default if view is None else view

    def __contains__(self, key) -> bool:
        return super().__contains__(key) or key in self._factories


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
        #: Stream URLs resolved ahead of being needed — see `StreamCache`.
        self.streams = yt.StreamCache(self.youtube)
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
        #: The last import's report and playlist, so a download that fails
        #: after the report is on screen can still be added to it.
        self.import_report: Optional[spotify.ImportReport] = None
        self.import_playlist = None
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

        #: The same for the taste profile, which is a few hundred kilobytes by
        #: the time somebody has imported a history. Writing it on every scroll
        #: of a reel is a lot of disk for a file nobody reads until next launch.

        self.setWindowTitle(APP_NAME)
        self.resize(*self.preferences.window_size)

        #: Set before the rail is built, because building it consults this.
        self.sidebar_collapsed = False
        #: Pulled in because the window is narrow rather than because the user
        #: asked — remembered so widening it can undo exactly that.
        self._collapsed_for_width = False

        #: Built on first use — see `open_fullscreen_visualizer`.
        self.fullscreen_visualizer = None

        #: The list a streamed track came from, and where in it we are.
        self._streaming: list = []
        self._streaming_at = 0

        #: Audio CDs, streamed from the drive rather than played from files.
        self.cd = CdPlayer(self)
        self.cd.track_changed.connect(self._cd_track_changed)
        self.cd.state_changed.connect(self._cd_state_changed)
        self.cd.position_changed.connect(self._on_position)
        self.cd.failed.connect(lambda message: self.notify(message, "error"))
        self.cd.finished.connect(lambda: self.notify("Disc finished", "info"))

        self._build()
        self._shortcuts()
        self._connect_playback()
        self.apply_appearance(self.appearance)
        self.show_section(self.preferences.section)

        if self.preferences.sidebar_collapsed:
            # No animation on the way in: the sidebar should already be pulled
            # in when the window appears, not slide shut in front of the user.
            self.set_sidebar_collapsed(True, animate=False)

        self.playback.set_volume(self.preferences.volume)
        self._restore_session()

        #: The desktop's media controls — the bar, `playerctl`, media keys.
        #: None when there is no session bus to join, which is not an error.
        self.mpris = Mpris.start(self.playback, self, parent=self)

        QTimer.singleShot(900, self.check_unfinished_imports)

        if self.preferences.first_run:
            # Ask before the empty library is on screen — an empty list with no
            # explanation is the worst possible first impression.
            QTimer.singleShot(150, self.ask_for_music_folder)
        elif self.preferences.scan_on_start:
            QTimer.singleShot(400, self.rescan)
        if self.server.config.enabled:
            QTimer.singleShot(200, lambda: self.toggle_server(True))

        # Delayed so the window is up and interactive first — this is a network
        # call and nothing about it should hold up the launch.
        QTimer.singleShot(3000, self.check_for_updates)

    # ── Layout ────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.banner = Banner(self.appearance)
        layout.addWidget(self.banner)

        self.update_bar = UpdateBar(self.appearance)
        self.update_bar.update_requested.connect(self.install_update)
        layout.addWidget(self.update_bar)

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
        self.nav_rail = rail

        layout = QVBoxLayout(rail)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(2)

        # Pull-in toggle, first and always visible: collapsing a sidebar that
        # hides its own way back out is a one-way door.
        self.sidebar_toggle = QPushButton("☰")
        self.sidebar_toggle.setObjectName("SidebarToggle")
        self.sidebar_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sidebar_toggle.setToolTip("Hide the sidebar  (Ctrl+B)")
        self.sidebar_toggle.setFixedHeight(34)
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)
        layout.addWidget(self.sidebar_toggle)
        layout.addSpacing(6)

        #: Everything that goes away when the rail is pulled in.
        self.nav_items: list[QPushButton] = []

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
            self.nav_items.append(button)

        layout.addStretch(1)

        settings = QPushButton("  ⚙   Settings")
        settings.setObjectName("SidebarItem")
        settings.clicked.connect(self.open_settings)
        layout.addWidget(settings)
        self.nav_items.append(settings)

        return rail

    # ── Pulling the sidebar in and out ────────────────────────────

    def toggle_sidebar(self) -> None:
        self.set_sidebar_collapsed(not self.sidebar_collapsed)

    def set_sidebar_collapsed(self, collapsed: bool, *, animate: bool = True) -> None:
        """Narrow the rail to just its toggle, or put it back.

        Rows are hidden outright rather than squeezed: a column of labels
        clipped to a few pixels is noise, not navigation. The width the sidebar
        had is remembered so pulling it back out returns it to the size it was
        dragged to rather than a default.
        """
        if collapsed == self.sidebar_collapsed:
            return

        if collapsed:
            width = self.splitter.sizes()[0]
            if width > SIDEBAR_RAIL_WIDTH:
                self.preferences.sidebar_width = width

        self.sidebar_collapsed = collapsed
        for item in self.nav_items:
            item.setVisible(not collapsed)

        self.sidebar_toggle.setToolTip(
            "Show the sidebar  (Ctrl+B)" if collapsed else "Hide the sidebar  (Ctrl+B)"
        )

        target = SIDEBAR_RAIL_WIDTH if collapsed else max(150, self.preferences.sidebar_width)

        # Widened to span both ends for the duration. Left as they are, the
        # rail's own limits would clamp the very first animation step to the
        # final width and the movement would never be seen.
        self.nav_rail.setMinimumWidth(SIDEBAR_RAIL_WIDTH)
        self.nav_rail.setMaximumWidth(260)

        self._resize_sidebar(target, animate=animate)

    def _settle_sidebar(self) -> None:
        """Put the rail's width limits back, once it has finished moving.

        Restored rather than left open so that dragging the splitter cannot
        park the sidebar at a width where the labels are cut in half.
        """
        collapsed = self.sidebar_collapsed
        self.nav_rail.setMinimumWidth(SIDEBAR_RAIL_WIDTH if collapsed else 150)
        self.nav_rail.setMaximumWidth(SIDEBAR_RAIL_WIDTH if collapsed else 260)

    def _resize_sidebar(self, target: int, *, animate: bool) -> None:
        sizes = self.splitter.sizes()
        start, middle, queue = sizes[0], sizes[1], sizes[2]

        def apply(width: int) -> None:
            # The middle column absorbs the difference; the queue panel keeps
            # whatever it had, open or closed.
            self.splitter.setSizes([width, middle + (start - width), queue])

        if not animate:
            apply(target)
            self._settle_sidebar()
            return

        self._sidebar_animation = QVariantAnimation(self)
        self._sidebar_animation.setStartValue(start)
        self._sidebar_animation.setEndValue(target)
        self._sidebar_animation.setDuration(SIDEBAR_ANIMATION_MS)
        self._sidebar_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._sidebar_animation.valueChanged.connect(lambda value: apply(int(value)))
        self._sidebar_animation.finished.connect(self._settle_sidebar)
        self._sidebar_animation.start()

    def _sections(self) -> QWidget:
        self.stack = QStackedWidget()
        self.views = Sections(self.stack.addWidget)

        # YouTube is YouTube: the site itself, in the web view, because that is
        # the only thing that looks exactly like YouTube. Ads and telemetry are
        # stripped by the request interceptor before the page renders.
        #
        # Built on first open rather than at startup: a browser engine is by
        # far the most expensive thing in the app to start, and it used to be
        # started — and pointed at youtube.com — for everyone, including the
        # people who never leave the library.
        self.views.add_lazy("watch", self._build_watch)

        # A player for video that is not YouTube's — a film on a disc, a file.
        # It has no nav entry of its own; the disc reader switches to it.
        self.views.add_lazy("player", self._build_video)

        disc = DiscView(self.appearance, self.preferences.downloads_path)
        disc.status.connect(self.notify)
        disc.ripped.connect(self._ripped)
        disc.burn_requested.connect(self.burn_queue)
        disc.play_disc_requested.connect(self.play_disc)
        disc.drive_needed.connect(self._free_the_drive)
        disc.watch_requested.connect(self.watch_disc)
        self._register("disc", disc)


        library_view = LibraryView(self.library, self.appearance)
        library_view.play_requested.connect(self.play_track)
        library_view.menu_requested.connect(self.open_track_menu)
        library_view.scan_requested.connect(self.rescan)
        self._register("library", library_view)

        albums = AlbumsView(self.library, self.appearance)
        albums.play_requested.connect(self.play_track)
        albums.menu_requested.connect(self.open_track_menu)
        albums.tracklist_wanted.connect(self.look_up_tracklist)
        albums.fetch_requested.connect(self.fetch_missing_track)
        self._register("albums", albums)

        playlists = PlaylistsView(self.library, self.playlists, self.appearance)
        playlists.play_requested.connect(self.play_track)
        playlists.menu_requested.connect(self.open_track_menu)
        self._register("playlists", playlists)

        importer = ImportView(self.appearance)
        importer.import_requested.connect(self.import_spotify)
        importer.resume_requested.connect(self.resume_import)
        importer.force_resume_requested.connect(
            lambda job: self.resume_import(job, ignore_wait=True))
        importer.retry_failed_requested.connect(self.retry_failed_downloads)
        importer.status.connect(self.notify)
        self._register("import", importer)

        downloads = DownloadsView(self.appearance)
        downloads.retry_requested.connect(self.retry_download)
        self._register("downloads", downloads)

        server_view = ServerView(self.server, self.appearance)
        server_view.toggled.connect(self.toggle_server)
        server_view.status.connect(self.notify)
        self._register("server", server_view)

        return self.stack

    def _register(self, key: str, view: QWidget) -> None:
        self.views.add(key, view)

    def _build_watch(self) -> QWidget:
        # Imported here rather than at the top of the file: pulling in
        # QtWebEngine costs tens of megabytes and a tenth of a second before
        # a single widget exists, and the whole point of building this tab
        # late is not to pay for a browser nobody opened.
        from rose_bouquet.ui.youtube_tab import YouTubeTab

        watch = YouTubeTab(self.appearance)
        watch.status.connect(self.notify)
        watch.download_requested.connect(self.download_watching)
        return watch

    def _build_video(self) -> QWidget:
        video = VideoStage(self.youtube, self.appearance)
        video.playback_requested.connect(self.playback.pause)
        video.status.connect(self.notify)
        return video

    @property
    def video(self) -> QWidget:
        """The video stage, built if this is the first thing to want it."""
        return self.views["player"]

    def _stop_video(self) -> None:
        """Silence the video stage, without starting one to silence.

        Every track played calls this, and building a media pipeline just to
        tell it to stop would undo the point of deferring it.
        """
        stage = self.views.built("player")
        if stage is not None:
            stage.stop()

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
            ("⏮", self.previous_track, "previous", "Previous  (Ctrl+Left)"),
            ("⏵", self.toggle_playback, "play", "Play or pause  (Space)"),
            ("⏭", self.next_track, "next", "Next  (Ctrl+Right)"),
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
            intensity=self.preferences.visualizer_intensity / 100,
            fps=self.preferences.visualizer_fps,
        )
        # Applied here as well as on every settings change: constructing one
        # only takes a single shape and the theme accent, so a saved stack and
        # a saved palette were both lost on every launch and only reappeared
        # if you happened to open Settings and touch something.
        self.visualizer.set_layers(self.preferences.layers())
        self.visualizer.set_palette(self.preferences.palette(self.appearance.theme.accent))
        self.visualizer.set_scales(self.preferences.visualizer_scales)

        self.visualizer.setVisible(self.preferences.visualizer)
        self.visualizer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.visualizer.setToolTip("Full screen  (F11)")
        self.visualizer.clicked.connect(self.open_fullscreen_visualizer)

        # The visualiser is clickable, but nothing about it says so. A button
        # beside it is the part people actually find.
        visualizer_row = QHBoxLayout()
        visualizer_row.setContentsMargins(0, 0, 0, 0)
        visualizer_row.setSpacing(4)
        visualizer_row.addWidget(self.visualizer, 1)

        self.fullscreen_button = QPushButton("⛶")
        self.fullscreen_button.setObjectName("Quiet")
        self.fullscreen_button.setToolTip("Full screen visualiser  (F11)")
        self.fullscreen_button.setFixedWidth(26)
        self.fullscreen_button.setVisible(self.preferences.visualizer)
        self.fullscreen_button.clicked.connect(self.open_fullscreen_visualizer)
        visualizer_row.addWidget(self.fullscreen_button)

        seek_column.addLayout(visualizer_row)

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
            ("Space", self.toggle_playback),
            ("Ctrl+Right", self.next_track),
            ("Ctrl+Left", self.previous_track),
            ("Ctrl+H", self.toggle_shuffle),
            ("Ctrl+R", self.cycle_repeat),
            ("Ctrl+Q", self.toggle_queue),
            ("Ctrl+B", self.toggle_sidebar),
            ("F11", self.open_fullscreen_visualizer),
            ("Esc", self.close_video),
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
        self.playback.finished.connect(self._queue_finished)
        self.playback.failed.connect(lambda message: self.notify(message, "error"))

        # Shuffle, repeat and volume can now be changed from outside this
        # window — a bar, `playerctl`, a headset button — so the controls have
        # to follow the player rather than assume they are the only way in.
        self.playback.queue_changed.connect(self._update_mode_buttons)
        self.playback.volume_changed.connect(self._on_volume_changed)

    def _queue_finished(self) -> None:
        # A streamed track finishing is the middle of a list, not the end of
        # one — the queue only ever held the single track being streamed.
        if self._step_stream(1):
            return
        self.notify("Queue finished", "info")

    def _on_volume_changed(self, volume: float) -> None:
        value = round(volume * 100)
        if value == self.volume.value():
            return
        # Without this the slider's own signal would set the volume straight
        # back, and a slow drag from a bar would fight itself.
        self.volume.blockSignals(True)
        self.volume.setValue(value)
        self.volume.blockSignals(False)

    # ── Sections ──────────────────────────────────────────────────

    def show_section(self, key: str) -> None:
        # The local feed, the Shorts reel, Following and Browse were all
        # replaced by the one YouTube tab. A preference saved before that
        # should land there rather than silently dumping you in the library.
        if key in ("feed", "shorts", "browse", "subscriptions", "youtube",
                   "webview"):
            key = "watch"

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
        self._stop_video()
        self.cd.stop()
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

    def _feed_artwork(self, track) -> None:
        """Hand the sleeve to the visualiser, for the shapes that draw it."""
        from PySide6.QtGui import QPixmap

        from rose_bouquet.core import artwork

        path = artwork.local_art(track) if track is not None else ""
        pixmap = QPixmap(path) if path else None
        self.visualizer.set_artwork(pixmap)
        if self.fullscreen_visualizer is not None:
            self.fullscreen_visualizer.visualizer.set_artwork(pixmap)

    def _on_track_changed(self, track: Optional[Track]) -> None:
        self._feed_artwork(track)
        if self.fullscreen_visualizer is not None:
            self.fullscreen_visualizer.set_track(
                track.display_title if track else "Nothing playing",
                track.display_artist if track else "",
            )

        self.now_art.set_track(track)
        self.now_title.setText(track.display_title if track else "Nothing playing")
        self.now_artist.setText(track.display_artist if track else "")
        self.setWindowTitle(
            f"{track.display_title} — {track.display_artist} · {APP_NAME}"
            if track else APP_NAME
        )
        self._refresh_queue()
        self._mark_playing()

    def _mark_playing(self) -> None:
        """Tell the section on screen which track is playing now.

        Not `refresh`: a section that rebuilds itself on every track change
        stalls for as long as its list is long, and scrolls itself back to the
        top while it is at it. Sections that only need to move a highlight say
        so by having `set_playing`.
        """
        current = self.stack.currentWidget()
        mark = getattr(current, "set_playing", None)
        if mark is None:
            self.refresh()
            return
        mark(self.playback.track.path if self.playback.track else "")

    def _on_state_changed(self, playing: bool) -> None:
        self.play_button.setText("⏸" if playing else "⏵")
        self.visualizer.set_live(playing)
        if self.fullscreen_visualizer is not None:
            self.fullscreen_visualizer.set_live(playing)

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

        # A folder that is not there is the likeliest reason a scan found
        # nothing, and it is worth saying before "no music found" — one of
        # those sends you to Settings to add a folder you already added.
        absent = self.library.missing_roots()
        if absent:
            names = ", ".join(str(root) for root in absent)
            self.notify(
                f"{names} is not there — is the drive mounted?"
                if len(absent) == 1 else f"These folders are not there: {names}",
                "error",
            )
            self.refresh()
            return

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

    def play_music_result(self, result: ytmusic.Result) -> None:
        """Stream something found while browsing, without downloading first."""
        self.play_candidate(Candidate(
            id=result.id, title=result.title, artist=result.artist,
            kind=result.kind, duration=result.duration,
            thumbnail=result.thumbnail, source="discover",
        ), context=[])

    def download_result(self, result: ytmusic.Result) -> None:
        request = ytmusic.DownloadRequest(
            video_id=result.id, title=result.title,
            artist=result.artist, album=result.album,
            fmt=self.preferences.download_format,
        )
        self._download(request)

    def retry_download(self, request: ytmusic.DownloadRequest) -> None:
        """The Retry button on a failed row.

        It used to be wired to `download_result`, which takes a search Result
        and reads `.id` — a row hands back the DownloadRequest it was started
        from, which spells that field `video_id`. So every press raised inside
        the signal and the button did nothing at all, silently.

        Going straight to `_download` also keeps the chosen format and the rest
        of the original request rather than rebuilding half of it.
        """
        if request is None:
            return
        # The old row is still marked failed, and a stale one confuses both the
        # duplicate check and the summary count.
        self.views["downloads"].entries.pop(request.video_id, None)
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

        if self.already_have(key):
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
            # A crash in the worker is a failed download like any other, and
            # used to stop at a grey row: the import never heard about it, so
            # the track was neither downloaded nor listed as missing.
            on_error=lambda message, key=key, label=label: self._download_failed(
                key, label, message, request),
            pool=self.downloads_pool,
        )

    def already_have(self, video_id: str) -> bool:
        """Whether this recording is in the library *and* still on disk.

        Both halves matter. A library entry outlives the file it names, so
        asking the library alone meant that once the music was gone every
        download of a track still listed was refused as one you already had:
        an import of nine hundred fetched the twenty that happened not to be
        listed and declined the rest, one polite notification at a time.
        """
        if not video_id:
            return False
        track = next((t for t in self.library.tracks.values()
                      if t.source_id == video_id), None)
        return track is not None and Path(track.path).exists()

    def _downloaded(self, outcome: ytmusic.DownloadResult, key: str, label: str) -> None:
        downloads = self.views["downloads"]

        if not outcome.ok:
            self._download_failed(key, label, outcome.error, outcome.request)
            return

        downloads.note(key, label, 1.0, "done", None)

        if self.import_job is not None:
            self.import_job.note_done(key, outcome.path)
            self._import_save.start()

        # A retry that worked takes the track back off the failed list.
        self._note_import_recovery(key)

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

    def _download_failed(self, key: str, label: str, error: str, request) -> None:
        """One place for a download that did not land, however it failed.

        The row goes red, the import record remembers, and — the part that was
        missing — the track joins the list of things that did not make it, next
        to the ones nothing could be found for. A toast is not a record: it is
        gone in six seconds and the song is gone with it.
        """
        error = (error or "").strip()
        self.views["downloads"].note(key, label, 0.0, "failed", request)

        if self.import_job is not None:
            self.import_job.note_failed(key, error)
            self._import_save.start()

        self._note_import_download_failure(key, error)
        self.notify(f"Download failed: {error[:80] or label}", "error")

    def _track_for_download(self, key: str):
        """The imported track a download key belongs to, if it is from one."""
        report = getattr(self, "import_report", None)
        if report is None:
            return None
        for source, found in report.matched:
            if getattr(found, "id", "") == key:
                return source
        return None

    def _note_import_download_failure(self, key: str, error: str) -> None:
        """Add a failed download to the import's list of what did not arrive."""
        report = getattr(self, "import_report", None)
        track = self._track_for_download(key)
        if report is None or track is None:
            return

        report.note_download_failure(track, error)
        self.views["import"].show_report(report)

        # Saved with the playlist too, so closing the window does not lose it.
        playlist = getattr(self, "import_playlist", None)
        if playlist is not None:
            playlist.missing = report.missed_lines()
            self.playlists.save(playlist)

    def _note_import_recovery(self, key: str) -> None:
        """A retry landed — take the track back off that list."""
        report = getattr(self, "import_report", None)
        track = self._track_for_download(key)
        if report is None or track is None or not report.failed:
            return

        report.note_download_recovered(track)
        self.views["import"].show_report(report)

        playlist = getattr(self, "import_playlist", None)
        if playlist is not None:
            playlist.missing = report.missed_lines()
            self.playlists.save(playlist)

    def retry_failed_downloads(self) -> None:
        """Try again everything that matched but would not download."""
        report = getattr(self, "import_report", None)
        if report is None or not report.failed:
            self.notify("Nothing waiting to retry", "info")
            return

        wanted = {str(track) for track, _why in report.failed}
        again = [(source, found) for source, found in report.matched
                 if str(source) in wanted and getattr(found, "id", "")]
        if not again:
            self.notify("Nothing waiting to retry", "info")
            return

        self.notify(f"Retrying {len(again)} downloads", "info")
        for source, found in again:
            # Clear the old row first, or _download sees a "failed" entry and
            # the retry is refused as a duplicate.
            self.views["downloads"].entries.pop(found.id, None)
            self._download(ytmusic.DownloadRequest(
                video_id=found.id, title=source.title,
                artist=source.artist, fmt=self.preferences.download_format,
            ))
        self.show_section("downloads")

    def download_watching(self, url: str, page_title: str = "") -> None:
        """Download whatever the web view is showing.

        Works the same on music.youtube.com as on the video site — both spell a
        track `watch?v=…`, so the Music button and the Download button compose
        without either knowing about the other. This is what replaced the
        YouTube Music tab.

        The page title is only a label for the downloads row and a fallback:
        yt-dlp writes the real tags, and `track_from_download` prefers those.
        """
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url or "")
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if not video_id:
            self.notify("Open a video or a song first — that is not a watch page",
                        "warning")
            return

        # "Roygbiv - YouTube Music" is the tab title, not the song.
        title = (page_title or "").strip()
        for suffix in (" - YouTube Music", " - YouTube"):
            if title.endswith(suffix):
                title = title[:-len(suffix)].strip()

        self._download(ytmusic.DownloadRequest(
            video_id=video_id, title=title or video_id,
            fmt=self.preferences.download_format,
        ))
        self.show_section("downloads")

    # ── What an album actually contains ───────────────────────────

    def look_up_tracklist(self, key) -> None:
        """Ask the catalogue what is on this album, off the interface thread.

        Silent when it fails. The album already shows your files; a tracklist
        that did not arrive should cost nothing but the extra lines it would
        have added.
        """
        from rose_bouquet.core import tracklists

        artist, album = key

        def done(release) -> None:
            self.views["albums"].show_tracklist(key, release)

        def failed(message: str) -> None:
            logger.info("no tracklist for %r: %s", album, message)
            # Still tell the view, or it waits for an answer that never comes
            # and the album sits looking like the feature does not work.
            self.views["albums"].show_tracklist(key, None)

        tasks.run(lambda: tracklists.lookup(artist, album, self.ytmusic),
                  on_done=done, on_error=failed)

    def fetch_missing_track(self, title: str, artist: str, album: str) -> None:
        """Get a track the album has and the library does not.

        Matched with the same `best_match` the Spotify import uses rather than
        a second, differently-wrong matcher.
        """
        self.notify(f"Looking for {title}…", "info")

        def work():
            return self.ytmusic.best_match(title, artist)

        def done(found) -> None:
            if found is None:
                self.notify(f"Could not find {title} on YouTube Music", "warning")
                return
            self._download(ytmusic.DownloadRequest(
                video_id=found.id, title=title, artist=artist, album=album,
                fmt=self.preferences.download_format,
            ))
            self.show_section("downloads")

        tasks.run(work, on_done=done,
                  on_error=lambda message: self.notify(
                      f"Could not look that up: {message}", "error"))

    # ── Updates ───────────────────────────────────────────────────

    def check_for_updates(self) -> None:
        """Look once, quietly, and only speak up if there is something to say.

        A check that failed used to be indistinguishable from no update, which
        is fine here: this runs on its own without being asked, so a machine
        with no connection must stay silent rather than complain every launch.
        Settings → About still has the button that reports what went wrong.
        """
        import time

        from rose_bouquet.core import updates

        if not self.preferences.check_updates_on_start:
            return

        # Launching five times in an afternoon is one request, not five.
        since = time.time() - self.preferences.last_update_check
        if since < UPDATE_CHECK_INTERVAL:
            return

        self.preferences.set_persistent("last_update_check", time.time())

        def done(release) -> None:
            if release is None:
                return
            if updates.is_newer(release.version, updates.current_version()):
                self.update_bar.announce(release.version)

        tasks.run(updates.latest, on_done=done,
                  on_error=lambda message: logger.info(
                      "update check failed: %s", message))

    def install_update(self) -> None:
        """The Update now button on the bar."""
        from rose_bouquet.core import updates

        self.update_bar.working()

        def done(result) -> None:
            worked, message = result
            if worked:
                self.update_bar.finished(f"{message} Restart to use it.")
            else:
                self.update_bar.finished(message)

        tasks.run(updates.update, on_done=done,
                  on_error=lambda message: self.update_bar.finished(
                      f"Could not update: {message}"))

    # ── Watching and streaming ────────────────────────────────────

    def close_video(self) -> None:
        """Escape closes the video, and does nothing at all otherwise.

        Bound globally, so it must be harmless when there is no video — a
        shortcut that steals Escape from every dialog would be worse than no
        shortcut.
        """
        stage = self.views.built("player")
        if stage is not None and stage.isVisible():
            stage.close_player()

    def play_candidate(self, item: Candidate, context=None) -> None:
        """Stream something from the feed without downloading it first.

        The list it came from is remembered, because a streamed track cannot
        be queued the way a file can — every one needs its URL resolved first,
        so the queue holds exactly one track and Skip had nothing to move to.
        What follows it lives here instead.
        """
        # Taking the audio into the music player means the video is done.
        self._stop_video()
        self.cd.stop()

        self._streaming = list(context) or [item]
        self._streaming_at = next(
            (i for i, c in enumerate(self._streaming) if c.id == item.id), 0)

        self.notify(f"Loading {item.title}…", "info")

        def work():
            return self.streams.resolve(item.id, audio_only=True)

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

        tasks.run(work, on_done=play,
                  on_error=lambda message: self.notify(f"Stream failed: {message}", "error"))

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

        # Kept so a download that fails later can add itself to this report's
        # list rather than only appearing as a toast that scrolls away.
        self.import_report = report
        self.import_playlist = playlist

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

        # Deliberately staying put. This runs straight after an import, and the
        # import report — the list naming every song that was not found and
        # every one that would not download — is on the tab we were about to
        # jump away from. Being shown a progress bar instead of the list of
        # what you lost is how that list went unread. The downloads carry on in
        # the background either way, and the section is one click away.


    def resume_import(self, job, *, ignore_wait: bool = False) -> None:
        """Pick up an import that was cut short — reading as well as downloading.

        `ignore_wait` skips the recorded rate-limit window, which is the right
        thing to do after changing network: the limit was on the old connection.
        """
        # Before anything else: a row this job already ticked off, whose file
        # is no longer on disk, has to be asked for again. Otherwise resuming
        # an import that finished once can only ever report that it is done.
        regained = job.forget_downloads_that_are_gone()
        skipped = job.skip_already_downloaded(self.library)
        job.save()
        self.import_job = job

        if regained:
            self.notify(
                f"{regained} downloaded tracks are no longer on disk — "
                "fetching them again", "info")
        if skipped:
            self.notify(f"{skipped} were already in your library", "info")

        if ignore_wait:
            job.blocked_until = ""
            job.save()

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

    # ── Browsing music ────────────────────────────────────────────

    # ── Shorts ────────────────────────────────────────────────────

    # ── Discs ─────────────────────────────────────────────────────

    def toggle_playback(self) -> None:
        logger.info("transport: cd.track=%s -> %s", self.cd.track,
                    "cd" if self.cd.track is not None else "music")
        """Play or pause whichever of the two is actually running.

        A disc and a file cannot both be playing, so there is always exactly
        one right answer — but the buttons have to know which.
        """
        (self.cd if self.cd.track is not None else self.playback).toggle()

    def next_track(self) -> None:
        logger.info("skip: cd=%s streaming=%d queue=%d at=%s track=%s",
                    self.cd.track is not None, len(self._streaming),
                    len(self.playback.queue), self.playback.queue.position,
                    self.playback.track.display_title if self.playback.track else None)
        if self.cd.track is not None:
            self.cd.next()
        elif not self._step_stream(1):
            self.playback.next()

    def previous_track(self) -> None:
        logger.info("previous: cd=%s streaming=%d queue=%d at=%s",
                    self.cd.track is not None, len(self._streaming),
                    len(self.playback.queue), self.playback.queue.position)
        if self.cd.track is not None:
            self.cd.previous()
        elif not self._step_stream(-1):
            self.playback.previous()

    def _step_stream(self, direction: int) -> bool:
        """Move through a streamed list. False means there is no such list.

        Only applies while the thing playing *is* the streamed track — once
        something else is playing, the list is stale and the ordinary queue
        takes over again.
        """
        if not self._streaming:
            return False

        track = self.playback.track
        current = self._streaming[self._streaming_at]
        if track is None or track.source_id != current.id:
            self._streaming = []
            return False

        target = self._streaming_at + direction
        if not 0 <= target < len(self._streaming):
            return False

        self._streaming_at = target
        self.play_candidate(self._streaming[target], context=self._streaming)
        return True

    def _free_the_drive(self) -> None:
        """Stop playing the disc, because something else needs to read it.

        Only one thing can read a CD at a time. A rip starting while the disc
        is playing leaves both fighting over the drive — measured at nine to
        twelve seconds of stalling — and the rip may simply fail.
        """
        if self.cd.track is not None:
            self.cd.stop()
            self.notify("Stopped playing the disc — the drive is needed", "info")

    def play_disc(self, disc, device) -> None:
        """Play an audio CD, streamed from the drive.

        The music player steps aside rather than mixing with it — two things
        playing at once is nobody's intent — and the player bar follows the
        disc while it runs.
        """
        self.playback.pause()
        self._stop_video()
        self.cd.set_volume(self.playback.volume)
        self.cd.play_disc(disc, device)
        self.notify(f"Playing the disc — {len(disc)} tracks", "success")

    def _cd_track_changed(self, track) -> None:
        """Show the CD track in the player bar, where the music would be."""
        if track is None:
            self._on_track_changed(self.playback.track)
            return

        view = self.views["disc"]
        album = view.album_field.text().strip() if hasattr(view, "album_field") else ""
        artist = view.artist_field.text().strip() if hasattr(view, "artist_field") else ""

        self.now_art.set_track(None)
        self.now_title.setText(track.display_title)
        self.now_artist.setText(artist or album or "Audio CD")
        self.setWindowTitle(f"{track.display_title} — {APP_NAME}")

    def _cd_state_changed(self, playing: bool) -> None:
        self.play_button.setText("⏸" if playing else "⏵")
        self.visualizer.set_live(playing)
        if self.fullscreen_visualizer is not None:
            self.fullscreen_visualizer.set_live(playing)

    def watch_disc(self, device: str) -> None:
        """Watch a film disc — or play it, if it turns out to be music.

        An audio CD handed to the video player produces "could not open file",
        which reads as a broken app rather than as "that is a music disc". The
        check has to happen even when the disc has not been read yet, because
        pressing Watch is often the *first* thing anybody does.
        """
        view = self.views["disc"]

        if view.disc is not None and view.disc.tracks:
            self.play_disc(view.disc, Path(device) if device else None)
            return

        self.notify("Looking at the disc…", "info")

        def looked(disc) -> None:
            # It was music all along: play it rather than refusing.
            view.disc = disc
            view.refresh()
            self.play_disc(disc, Path(device) if device else None)

        def not_audio(_message: str) -> None:
            # No audio table of contents, so it is a data or film disc —
            # which is exactly what the video player is for.
            self.show_section("player")
            self.video.watch_file(device, title="Disc", artist="")

        tasks.run(lambda: optical.read_toc(Path(device) if device else None),
                  on_done=looked, on_error=not_audio)


    def _ripped(self, folder: str) -> None:
        """A rip finished; fold the new files into the library."""
        if folder not in self.library.folders:
            # Ripped into a folder outside the scanned set — usually a
            # per-album subfolder of the download directory, which the parent
            # scan will pick up anyway.
            logger.debug("ripped into %s", folder)
        self.rescan()

    def burn_queue(self) -> None:
        """Write what is in the play queue to a blank, in queue order."""
        tracks = [t for t in self.playback.queue.tracks if Path(t.path).exists()]
        if not tracks:
            self.notify("The queue is empty — add some music first", "warning")
            return

        absent = optical.missing("cdrskin", "ffmpeg")
        if absent:
            self.notify(absent[0].message.split("\n")[0], "error")
            return

        total = sum(t.duration for t in tracks)
        if not optical.fits_on_a_disc(total):
            self.notify(
                f"The queue is {int(total // 60)} minutes and a CD holds about "
                f"{optical.COMMON_MINUTES}", "warning")
            return

        drive = optical.default_drive()
        if drive is None:
            self.notify("No optical drive found", "error")
            return

        self._free_the_drive()
        burner = optical.CdBurner(drive.device)
        view = self.views["disc"]
        view._job = burner
        view._begin(f"Burning {len(tracks)} tracks…")

        paths = [Path(t.path) for t in tracks]

        def work(report):
            return burner.burn(paths, progress=lambda update: report(update))

        def done(result) -> None:
            view._end()
            view.refresh()
            self.notify(result.summary, "success")

        self.show_section("disc")
        tasks.run(work, on_progress=view._on_progress, on_done=done,
                  on_error=lambda message: (view._on_failed(message)))

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
        dialog.interests_changed.connect(self.refresh)
        dialog.server_changed.connect(self._server_settings_changed)
        dialog.exec()

    def _library_folders_changed(self) -> None:
        self.library.folders = list(self.preferences.folders)
        self.rescan()

    def _visualizer_changed(self) -> None:
        self.visualizer.setVisible(self.preferences.visualizer)
        self.fullscreen_button.setVisible(self.preferences.visualizer)
        self.visualizer.set_layers(self.preferences.layers())
        self.visualizer.alpha = self.preferences.visualizer_alpha / 100
        self.visualizer.set_blur(self.preferences.visualizer_blur)
        self.visualizer.set_intensity(self.preferences.visualizer_intensity / 100)
        self.visualizer.set_fps(self.preferences.visualizer_fps)
        self.visualizer.set_palette(self.preferences.palette(self.appearance.theme.accent))
        self.visualizer.set_scales(self.preferences.visualizer_scales)

        if self.fullscreen_visualizer is not None:
            self.fullscreen_visualizer.apply(
                shape=self._fullscreen_shape(),
                layers=self.preferences.layers(fullscreen=True),
                alpha=self.preferences.visualizer_alpha / 100,
                blur=self.preferences.visualizer_blur,
                intensity=self.preferences.visualizer_intensity / 100,
                fps=self.preferences.visualizer_fps,
            )
            self.fullscreen_visualizer.visualizer.set_palette(
                self.preferences.palette(self.appearance.theme.accent))
            self.fullscreen_visualizer.visualizer.set_scales(
                self.preferences.visualizer_scales)

        if self.preferences.visualizer:
            self.visualizer.start()
        else:
            self.visualizer.stop()

    # ── Full screen visualiser ────────────────────────────────────

    def _fullscreen_shape(self) -> Shape:
        try:
            return Shape(self.preferences.visualizer_fullscreen_shape)
        except ValueError:
            return Shape.RADIAL

    def open_fullscreen_visualizer(self) -> None:
        """Fill the screen with the visualiser. Escape, F11 or a click leaves.

        Built on first use rather than at startup: most sessions never open it,
        and an unused full-screen window is a second cava consumer and a second
        widget to keep in step with the settings.
        """
        if not cava.available():
            self.notify("cava is not installed, so there is nothing to show", "warning")
            return

        if self.fullscreen_visualizer is None:
            self.fullscreen_visualizer = FullscreenVisualizer(
                self.appearance,
                shape=self._fullscreen_shape(),
                alpha=self.preferences.visualizer_alpha / 100,
                blur=self.preferences.visualizer_blur,
                intensity=self.preferences.visualizer_intensity / 100,
                fps=self.preferences.visualizer_fps,
                parent=self,
            )
            self.fullscreen_visualizer.visualizer.set_layers(
                self.preferences.layers(fullscreen=True))
            self.fullscreen_visualizer.visualizer.set_palette(
                self.preferences.palette(self.appearance.theme.accent))
            self.fullscreen_visualizer.visualizer.set_scales(
                self.preferences.visualizer_scales)
            self.fullscreen_visualizer.previous_requested.connect(self.previous_track)
            self.fullscreen_visualizer.toggle_requested.connect(self.toggle_playback)
            self.fullscreen_visualizer.next_requested.connect(self.next_track)
            self.fullscreen_visualizer.closed.connect(self._fullscreen_closed)

        track = self.playback.track
        self.fullscreen_visualizer.set_track(
            track.display_title if track else "Nothing playing",
            track.display_artist if track else "",
        )
        self.fullscreen_visualizer.set_live(self.playback.playing)
        self.fullscreen_visualizer.open()

    def _fullscreen_closed(self) -> None:
        # The small one shares cava with it and had its timer left running, so
        # there is nothing to restart — only the live flag to hand back.
        self.visualizer.set_live(self.playback.playing)

    def _server_settings_changed(self) -> None:
        self.server.config = self.preferences.server_config()
        if self.server.running:
            ok, message = self.server.restart()
            self.notify(message, "success" if ok else "error")

    def apply_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance
        set_active_style(appearance.style)
        self.setStyleSheet(appearance.stylesheet())

        if self.fullscreen_visualizer is not None:
            self.fullscreen_visualizer.restyle(appearance)
            self.fullscreen_visualizer.visualizer.set_palette(
                self.preferences.palette(appearance.theme.accent))

        self.visualizer.set_palette(self.preferences.palette(appearance.theme.accent))

        for widget in (*self.views.values(), self.banner, self.update_bar,
                       self.visualizer, self.now_art):
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

    def resizeEvent(self, event) -> None:
        """Pull the sidebar in when there is no room for it.

        The user's own preference is left alone: this is the window reacting
        to its size, not a setting being changed behind their back, so making
        it wide again restores whatever they had chosen.
        """
        super().resizeEvent(event)

        # The event's size, not the widget's: during a resize the widget may
        # still report the width it is coming *from*.
        narrow = event.size().width() < NARROW_WIDTH
        if narrow and not self.sidebar_collapsed:
            self._collapsed_for_width = True
            self.set_sidebar_collapsed(True, animate=False)
        elif not narrow and getattr(self, "_collapsed_for_width", False):
            self._collapsed_for_width = False
            if not self.preferences.sidebar_collapsed:
                self.set_sidebar_collapsed(False, animate=False)

    def closeEvent(self, event) -> None:
        # Anything still running is about to lose the widgets it reports to.
        tasks.cancel_all()
        self._save_session()
        if self.import_job is not None:
            self.import_job.save()
        self.playback.stop()
        self._stop_video()
        self.streams.close()
        self.cd.stop()
        self.visualizer.stop()
        self.server.stop()
        if self.mpris is not None:
            self.mpris.stop()

        self.preferences.window_size = (self.width(), self.height())
        sizes = self.splitter.sizes()
        # Pulled in, the rail's width is not a width the user chose — saving it
        # would lose the one they dragged to.
        if sizes and not self.sidebar_collapsed:
            self.preferences.sidebar_width = sizes[0]
        # Not saved if it was the window's width that pulled it in: that is a
        # fact about this session, not something the user picked.
        if not self._collapsed_for_width:
            self.preferences.sidebar_collapsed = self.sidebar_collapsed
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
