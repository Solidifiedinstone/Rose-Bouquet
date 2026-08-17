"""The video surface — watching YouTube inside the app.

A music player that can only take the audio out of YouTube is half a client,
and a tab called Watch that plays only sound is a broken promise. This is the
picture half: a video widget, a transport under it, and nothing else. It knows
how to play one video and how to get out of the way.

Deliberately *only* the player. Search, results and the feed live in the Watch
tab itself, so this is a panel that tab shows above its list rather than a
second screen with its own copy of the search box — one list to keep working,
not two that drift apart.

Two things make it a *music* app's video player rather than a browser tab:

  - **Audio-only is one button away.** Most of what gets watched here is a song
    with a still image over it, and playing that as video wastes bandwidth and
    battery for nothing. Switching drops the picture and keeps the position.
  - **It hands over to the music player cleanly.** Starting a video asks the
    music to pause rather than playing both at once, and the queue is still
    there when the video ends.

Streams are resolved with yt-dlp and handed to Qt as a URL. Only *progressive*
formats are asked for — a single file with both streams in it — because
QMediaPlayer cannot mux a separate video and audio track, and a player that
silently plays video with no sound is worse than one that picks a lower quality.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.ui import tasks
from rose_bouquet.ui.theme import Appearance

logger = logging.getLogger(__name__)


class VideoStage(QWidget):
    """A video surface with a transport. Hidden until something is watched."""

    #: Asks whatever else is making noise to stop. Emitted before a stream is
    #: even resolved, so the music stops when you press play, not seconds later
    #: when the network gets round to answering.
    playback_requested = Signal()
    closed = Signal()
    status = Signal(str, str)
    #: The thing being watched reached its end. A reel uses this to roll on.
    finished = Signal()

    def __init__(self, youtube, appearance: Appearance,
                 parent: Optional[QWidget] = None) -> None:
        """`youtube` resolves a video id to a stream, and may be None.

        It is given one now: the YouTube tab plays here rather than in a
        browser, which is what makes it ad-free. None is still allowed, for the
        disc reader and for playing a video file — neither needs resolving.
        """
        super().__init__(parent)
        self.youtube = youtube
        self.appearance = appearance

        #: The Candidate being watched, or None.
        self.current = None
        self.audio_only = False
        self._seeking = False
        #: The frameless window holding the picture while fullscreen.
        self._fullscreen_window = None

        self._output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self._output)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.positionChanged.connect(self._on_position)
        self.player.errorOccurred.connect(self._on_error)

        self._build()
        self.setVisible(False)

    def _build(self) -> None:
        # The picture with its transport under it. There was an up-next column
        # beside it once, filled by the local ranker; YouTube's own site does
        # that job now, so the picture gets the whole width.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 10)
        outer.setSpacing(14)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        outer.addLayout(layout, 1)

        self.video = QVideoWidget()
        # Double-click the picture, which is what every other player does.
        self.video.mouseDoubleClickEvent = (
            lambda _event: self.toggle_fullscreen())
        self.video.setMinimumHeight(320)
        self.player.setVideoOutput(self.video)
        layout.addWidget(self.video, 1)
        # The layout the picture actually lives in, which fullscreen takes it
        # out of and puts it back into.
        self._stage_layout = layout

        self.caption = QLabel()
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)

        layout.addLayout(self._transport())


    # ── Up next ───────────────────────────────────────────────────

    def _transport(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        # Starts as "play": showing a pause icon before anything is playing
        # says the opposite of what is true, and stays wrong for as long as
        # the stream takes to fail.
        self.play_button = QPushButton("⏵")
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

        self.fullscreen_button = QPushButton("⛶")
        self.fullscreen_button.setObjectName("Quiet")
        self.fullscreen_button.setToolTip(
            "Fullscreen  (F, or double-click the picture)\n\nEsc comes back.")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        row.addWidget(self.fullscreen_button)

        close = QPushButton("✕")
        close.setObjectName("Quiet")
        close.setToolTip("Close the video  (Esc)")
        close.clicked.connect(self.close_player)
        row.addWidget(close)

        return row

    # ── Fullscreen ────────────────────────────────────────────────

    def toggle_fullscreen(self) -> None:
        """Put the picture on the whole screen, and bring it back.

        The video widget is reparented to a frameless window of its own rather
        than the whole stage being maximised. Maximising the stage would take
        the window's chrome and the transport with it, and a fullscreen video
        that still has a title bar above it is the thing people mean when they
        say fullscreen does not work.

        Reparenting a QVideoWidget mid-playback is safe — the sink follows the
        widget — but it must be put back on the way out or the stage is left
        with a hole where the picture was.
        """
        if self._fullscreen_window is not None:
            return self.leave_fullscreen()

        window = QWidget(None)
        window.setWindowTitle(self.caption.text() or "Rose Bouquet")
        window.setStyleSheet("background: black;")
        layout = QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)

        self._stage_layout.removeWidget(self.video)
        layout.addWidget(self.video)

        # Esc and F both leave, and so does double-clicking, because every one
        # of those is what somebody tries first.
        for key in (Qt.Key_Escape, Qt.Key_F):
            QShortcut(QKeySequence(key), window, activated=self.leave_fullscreen)

        window.showFullScreen()
        self.video.setFocus()
        self._fullscreen_window = window

    def leave_fullscreen(self) -> None:
        if self._fullscreen_window is None:
            return

        window, self._fullscreen_window = self._fullscreen_window, None
        window.layout().removeWidget(self.video)
        # Back above the caption and the transport, where it was.
        self._stage_layout.insertWidget(0, self.video, 1)
        self.video.show()
        window.close()
        window.deleteLater()

    # ── Watching ──────────────────────────────────────────────────

    def watch(self, item, *, audio_only: Optional[bool] = None,
              url: Optional[str] = None) -> None:
        """Play a Candidate, resolving its stream unless one is handed over.

        `url` is what makes a reel feel instant: the next one was resolved
        while the last was playing, so scrolling costs a repaint rather than a
        round trip.
        """
        self.current = item
        if audio_only is not None:
            self.audio_only = audio_only
            self.audio_button.setText("Show video" if self.audio_only else "Audio only")

        self.playback_requested.emit()      # the music player steps aside
        self.setVisible(True)
        self.video.setVisible(not self.audio_only)
        self.caption.setText(f"Loading {item.title}…")

        wanted_audio_only = self.audio_only
        video_id = item.id

        # Whatever was playing is released before the next one starts, so a
        # long reel does not accumulate decoders it will never use again.
        self.player.stop()

        if url:
            self._play(url, item)
            return

        tasks.run(
            lambda: self.youtube.stream_url(video_id, audio_only=wanted_audio_only),
            on_done=lambda url: self._play(url, item),
            on_error=lambda message: self.status.emit(
                f"Could not play that: {message}", "error"),
        )

    def watch_file(self, path: str, *, title: str = "", artist: str = "") -> None:
        """Play something that is already local — a file, or a disc device.

        No stream to resolve and nothing to look up, so this skips the whole
        YouTube path. A disc is handed over as its device node, which Qt's
        ffmpeg backend opens directly for an unencrypted video disc.
        """
        self.current = None
        self.playback_requested.emit()
        self.setVisible(True)
        self.audio_only = False
        self.audio_button.setText("Audio only")
        self.video.setVisible(True)
        self.caption.setText(title or Path(path).name)

        source = QUrl.fromLocalFile(path) if Path(path).exists() else QUrl(path)
        self.player.setSource(source)
        self.player.play()

    def _play(self, url: str, item) -> None:
        # The stream arrives from a background job, and by then the user may
        # have closed the panel or started something else entirely.
        if self.current is None or item.id != self.current.id:
            return

        if not url:
            self.caption.setText("No playable stream for this one.")
            self.status.emit("YouTube did not offer a stream for that video", "error")
            return

        self.caption.setText(f"{item.title}\n{item.artist}")
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
        position = self.player.position()
        self.audio_only = not self.audio_only
        self.audio_button.setText("Show video" if self.audio_only else "Audio only")
        self.video.setVisible(not self.audio_only)

        # A local file or a disc has one stream carrying both tracks, so there
        # is nothing to re-fetch — hiding the picture is the whole operation.
        # Without this the button did nothing at all for anything not from
        # YouTube, silently, which reads as a broken button.
        if self.current is None:
            return

        item = self.current
        wanted = self.audio_only

        def resume(url: str) -> None:
            if not url or self.current is None or self.current.id != item.id:
                return
            self.player.setSource(QUrl(url))
            self.player.setPosition(position)
            self.player.play()

        tasks.run(lambda: self.youtube.stream_url(item.id, audio_only=wanted),
                  on_done=resume)

    def close_player(self) -> None:
        self.player.stop()
        self.setVisible(False)
        self.current = None
        # Cleared rather than left standing: suggestions for a video that is no
        # longer playing are stale the moment the panel closes, and they would
        # be the first thing on screen the next time it opens.
        self.closed.emit()

    def stop(self) -> None:
        self.player.stop()

    @property
    def playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    # ── Player signals ────────────────────────────────────────────

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.finished.emit()

    def _on_state(self, _state) -> None:
        self.play_button.setText("⏸" if self.playing else "⏵")

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
        self.caption.setText("This would not play.")
        self.play_button.setText("⏵")
        self.status.emit(
            "That would not play. If it is a disc, it may be an audio CD or "
            "copy-protected; if it is a video, try Audio only.",
            "error",
        )

    def apply_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance
        theme = appearance.theme
        self.caption.setStyleSheet(f"color: {theme.text}; background: transparent;")
        # The rows hold thumbnails, and a thumbnail's placeholder is drawn in
        # the palette it was built with — so they are rebuilt rather than
        # restyled, which is also how the feed handles a theme change.


#: Wide enough for a thumbnail and two lines of title, narrow enough that the
#: picture is still the biggest thing on screen.
RELATED_WIDTH = 320


def _clock(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"
