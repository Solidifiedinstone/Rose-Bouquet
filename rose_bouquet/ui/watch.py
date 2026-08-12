"""Watching YouTube — video, in the app, with the music side still attached.

A music player that can only take the audio out of YouTube is half a client.
This is the other half: search, a wall of thumbnails, and a real video surface
with a transport under it.

Two things make it a *music* app's video player rather than a browser tab:

  - **Audio-only is one button away.** Most of what gets watched here is a song
    with a still image over it, and playing that as video wastes bandwidth and
    battery for nothing. Switching drops the picture and keeps the position.
  - **It hands over to the music player cleanly.** Starting a video pauses the
    music rather than playing both at once, and the queue is still there when
    the video ends.

Streams are resolved with yt-dlp and handed to Qt as a URL. Only *progressive*
formats are asked for — a single file with both streams in it — because
QMediaPlayer cannot mux a separate video and audio track, and a player that
silently plays video with no sound is worse than one that picks a lower quality.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core.youtube import Video, YouTube
from rose_bouquet.ui import tasks
from rose_bouquet.ui.theme import Appearance

logger = logging.getLogger(__name__)


class WatchView(QWidget):
    """Search, results, and a video player."""

    #: Ask the music player to get out of the way.
    playback_requested = Signal()
    download_requested = Signal(object)      # Video
    subscribe_requested = Signal(object)     # Video
    like_toggled = Signal(object)            # Video
    status = Signal(str, str)

    def __init__(self, youtube: YouTube, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.youtube = youtube
        self.appearance = appearance
        self.results: list[Video] = []
        self.current: Optional[Video] = None
        self.loading = False
        self.audio_only = False
        self._seeking = False

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda _d: self._on_position(self.player.position()))
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(self._on_error)

        self._build()

    # ── Layout ────────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Search ───────────────────────────────────────────────
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 14, 18, 8)
        header_layout.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search YouTube…")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.run_search)
        header_layout.addWidget(self.search, 1)

        find = QPushButton("Search")
        find.setObjectName("Primary")
        find.clicked.connect(self.run_search)
        header_layout.addWidget(find)

        self.back = QPushButton("← Results")
        self.back.setObjectName("Quiet")
        self.back.clicked.connect(self.close_player)
        self.back.setVisible(False)
        header_layout.addWidget(self.back)

        layout.addWidget(header)

        # ── The video ────────────────────────────────────────────
        self.stage = QWidget()
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(14, 0, 14, 10)
        stage_layout.setSpacing(8)

        self.video = QVideoWidget()
        self.video.setMinimumHeight(360)
        self.player.setVideoOutput(self.video)
        stage_layout.addWidget(self.video, 1)

        self.caption = QLabel()
        self.caption.setWordWrap(True)
        stage_layout.addWidget(self.caption)

        stage_layout.addLayout(self._transport())
        self.stage.setVisible(False)
        layout.addWidget(self.stage, 1)

        # ── Results ──────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 0, 14, 24)
        self.body_layout.setSpacing(2)
        scroll.setWidget(self.body)

        self.scroll = scroll
        layout.addWidget(scroll, 1)

        self.refresh()

    def _transport(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.play_button = QPushButton("⏸")
        self.play_button.setObjectName("Primary")
        self.play_button.setFixedWidth(46)
        self.play_button.clicked.connect(self.toggle)
        row.addWidget(self.play_button)

        self.elapsed = QLabel("0:00")
        self.elapsed.setObjectName("Subtle")
        row.addWidget(self.elapsed)

        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek.sliderReleased.connect(self._seek_released)
        row.addWidget(self.seek, 1)

        self.total = QLabel("0:00")
        self.total.setObjectName("Subtle")
        row.addWidget(self.total)

        self.audio_button = QPushButton("Audio only")
        self.audio_button.setToolTip(
            "Hide the picture and keep the sound.\n\n"
            "Whether this also saves bandwidth depends on what YouTube offers: "
            "when a separate audio stream is available it is used, and when "
            "only a combined one is served the video is fetched and not shown."
        )
        self.audio_button.clicked.connect(self.toggle_audio_only)
        row.addWidget(self.audio_button)

        for text, signal, tip in (
            ("↓", self.download_requested, "Download the audio"),
            ("☆", self.subscribe_requested, "Follow this channel"),
            ("♡", self.like_toggled, "Like — this shapes your feed"),
        ):
            button = QPushButton(text)
            button.setObjectName("Quiet")
            button.setToolTip(tip)
            button.clicked.connect(
                lambda _checked=False, signal=signal: self.current and signal.emit(self.current)
            )
            row.addWidget(button)

        return row

    # ── Searching ─────────────────────────────────────────────────

    def run_search(self) -> None:
        query = self.search.text().strip()
        if not query:
            return

        self.loading = True
        self.refresh()
        tasks.run(
            self.youtube.search, query, 24,
            on_done=self._searched,
            on_error=lambda message: self.status.emit(f"Search failed: {message}", "error"),
        )

    def _searched(self, videos: list) -> None:
        self.loading = False
        self.results = videos or []
        if not self.results:
            self.status.emit("Nothing found", "warning")
        self.refresh()

    def show_videos(self, videos: list[Video], *, note: str = "") -> None:
        """Show a list from elsewhere — a channel's uploads, say."""
        self.loading = False
        self.results = videos
        if note:
            self.status.emit(note, "info")
        self.refresh()

    # ── Playing ───────────────────────────────────────────────────

    def watch(self, video: Video, *, audio_only: Optional[bool] = None) -> None:
        """Resolve a stream and play it."""
        self.current = video
        if audio_only is not None:
            self.audio_only = audio_only

        self.playback_requested.emit()      # the music player steps aside
        self.stage.setVisible(True)
        self.scroll.setVisible(False)
        self.back.setVisible(True)
        self.video.setVisible(not self.audio_only)
        self.caption.setText(f"Loading {video.title}…")

        wanted_audio_only = self.audio_only

        def work():
            return self.youtube.stream_url(video.id, audio_only=wanted_audio_only)

        tasks.run(
            work,
            on_done=lambda url: self._play(url, video),
            on_error=lambda message: self.status.emit(f"Could not play that: {message}", "error"),
        )

    def _play(self, url: str, video: Video) -> None:
        if not url:
            self.caption.setText("No playable stream for this one.")
            self.status.emit("YouTube did not offer a stream for that video", "error")
            return

        self.caption.setText(f"{video.title}\n{video.channel}")
        self.player.setSource(QUrl(url))
        self.player.play()

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def toggle_audio_only(self) -> None:
        """Swap between video and audio, keeping the position.

        The stream has to be fetched again — they are different files — so the
        position is carried across by hand rather than lost.
        """
        if self.current is None:
            return

        position = self.player.position()
        self.audio_only = not self.audio_only
        self.audio_button.setText("Show video" if self.audio_only else "Audio only")
        self.video.setVisible(not self.audio_only)

        video = self.current
        wanted = self.audio_only

        def work():
            return self.youtube.stream_url(video.id, audio_only=wanted)

        def resume(url: str) -> None:
            if not url:
                return
            self.player.setSource(QUrl(url))
            self.player.setPosition(position)
            self.player.play()

        tasks.run(work, on_done=resume)

    def close_player(self) -> None:
        self.player.stop()
        self.stage.setVisible(False)
        self.scroll.setVisible(True)
        self.back.setVisible(False)
        self.current = None

    def stop(self) -> None:
        self.player.stop()

    # ── Player signals ────────────────────────────────────────────

    def _on_state(self, _state) -> None:
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("⏸" if playing else "⏵")

    def _on_position(self, position: int) -> None:
        duration = self.player.duration()
        if duration and not self._seeking:
            self.seek.setValue(int(position / duration * 1000))
        self.elapsed.setText(_clock(position))
        self.total.setText(_clock(duration))

    def _seek_released(self) -> None:
        self._seeking = False
        duration = self.player.duration()
        if duration:
            self.player.setPosition(int(self.seek.value() / 1000 * duration))

    def _on_error(self, error, message: str = "") -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        logger.warning("video playback failed: %s", message)
        self.status.emit(
            "That stream would not play — try Audio only, which uses a simpler format.",
            "error",
        )

    # ── Results ───────────────────────────────────────────────────

    def refresh(self, *_args) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if self.loading:
            self.body_layout.addWidget(self._message("Searching…"))
            self.body_layout.addStretch(1)
            return

        if not self.results:
            self.body_layout.addWidget(self._message(
                "Search YouTube above.\n\nAnything you find can be watched here, "
                "played as audio only, downloaded, or followed."
            ))
            self.body_layout.addStretch(1)
            return

        grid_holder = QWidget()
        grid = QGridLayout(grid_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        for index, video in enumerate(self.results):
            grid.addWidget(self._card(video), index // 3, index % 3)

        self.body_layout.addWidget(grid_holder)
        self.body_layout.addStretch(1)

    def _card(self, video: Video) -> QWidget:
        theme = self.appearance.theme

        card = QWidget()
        card.setObjectName("Card")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(
            f"#Card {{ background: transparent;"
            f" border-radius: {self.appearance.style.radius}px; }}"
            f"#Card:hover {{ background-color: {theme.panel}; }}"
        )

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        title = QLabel(video.title)
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {theme.text}; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(f"{video.channel} · {video.clock}" if video.duration else video.channel)
        subtitle.setObjectName("Subtle")
        layout.addWidget(subtitle)

        buttons = QHBoxLayout()
        watch = QPushButton("Watch")
        watch.clicked.connect(lambda _c=False, v=video: self.watch(v, audio_only=False))
        buttons.addWidget(watch)

        listen = QPushButton("Listen")
        listen.setObjectName("Quiet")
        listen.setToolTip("Play the audio without the video")
        listen.clicked.connect(lambda _c=False, v=video: self.watch(v, audio_only=True))
        buttons.addWidget(listen)

        get = QPushButton("↓")
        get.setObjectName("Quiet")
        get.setToolTip("Download the audio")
        get.clicked.connect(lambda _c=False, v=video: self.download_requested.emit(v))
        buttons.addWidget(get)

        layout.addLayout(buttons)
        return card

    def _message(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Subtle")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setContentsMargins(20, 60, 20, 0)
        return label

    # ── Appearance ────────────────────────────────────────────────

    def stage_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance

    def apply_appearance(self, appearance: Appearance) -> None:
        self.stage_appearance(appearance)
        self.refresh()


def _clock(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"
