"""The audio engine: a queue, a QMediaPlayer, and the rules that join them.

`core.playqueue` decides what plays next and knows nothing about audio;
`QMediaPlayer` plays audio and knows nothing about queues. This is the seam
between them, and the only place in the app that touches both.

Qt's own `QMediaPlaylist` was removed in Qt 6 and its replacement handles
neither shuffle nor repeat the way people expect, which is the other reason the
queue is ours: the behaviour people actually want — shuffle as a shuffled order,
back meaning restart-then-previous — is a handful of rules that have to live
somewhere testable.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from rose_bouquet.core.library import Library, Track
from rose_bouquet.core.playqueue import Queue, Repeat

logger = logging.getLogger(__name__)

#: A track counts as played once this much of it has gone by — the same rule
#: scrobblers use, and the reason a skipped intro does not inflate a play count.
PLAYED_FRACTION = 0.5
PLAYED_SECONDS = 120


class Playback(QObject):
    """Plays the queue."""

    track_changed = Signal(object)      # Track | None
    state_changed = Signal(bool)        # playing?
    position_changed = Signal(int, int)  # position ms, duration ms
    queue_changed = Signal()
    finished = Signal()                 # the queue ran out

    def __init__(self, library: Library, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.library = library
        self.queue = Queue()

        self._output = QAudioOutput()
        self._output.setVolume(0.8)

        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._output)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(lambda _d: self._on_position(self._player.position()))
        self._player.errorOccurred.connect(self._on_error)

        #: Set while a track is being loaded, so the end-of-media signal from
        #: the *previous* track cannot advance the queue a second time.
        self._loading = False
        self._counted = False

        # Qt emits positionChanged very often; the interface does not need it
        # more than a few times a second.
        self._throttle = QTimer(self)
        self._throttle.setInterval(200)
        self._throttle.setSingleShot(True)

    # ── What is playing ───────────────────────────────────────────

    @property
    def track(self) -> Optional[Track]:
        return self.queue.current

    @property
    def playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def position(self) -> int:
        return self._player.position()

    @property
    def duration(self) -> int:
        return self._player.duration() or (self.track.duration * 1000 if self.track else 0)

    @property
    def elapsed_seconds(self) -> float:
        return self._player.position() / 1000

    # ── Controls ──────────────────────────────────────────────────

    def play_tracks(self, tracks: list[Track], start: int = 0) -> None:
        """Replace the queue with these and start playing."""
        self.queue.set_tracks(tracks, start)
        self.queue_changed.emit()
        self._load(self.queue.current, play=True)

    def enqueue(self, tracks: list[Track]) -> None:
        was_empty = not len(self.queue)
        self.queue.enqueue(tracks)
        self.queue_changed.emit()
        if was_empty:
            self._load(self.queue.current, play=True)

    def play_next(self, tracks: list[Track]) -> None:
        was_empty = not len(self.queue)
        self.queue.play_next(tracks)
        self.queue_changed.emit()
        if was_empty:
            self._load(self.queue.current, play=True)

    def toggle(self) -> None:
        if self.playing:
            self._player.pause()
        elif self.track is not None:
            self._player.play()

    def play(self) -> None:
        if self.track is not None:
            self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def stop(self) -> None:
        self._player.stop()

    def next(self) -> None:
        """Skip. A manual skip moves on even with repeat-one set."""
        track = self.queue.next(manual=True)
        if track is None:
            self._player.stop()
            self.finished.emit()
            return
        self._load(track, play=True)

    def previous(self) -> None:
        track = self.queue.previous(elapsed=self.elapsed_seconds)
        if track is None:
            return
        # Pressing back a few seconds in restarts the track rather than moving.
        if track is self.queue.current and self.elapsed_seconds >= 3.0:
            self._player.setPosition(0)
            self._player.play()
            return
        self._load(track, play=True)

    def jump_to(self, index: int) -> None:
        track = self.queue.jump_to(index)
        if track is not None:
            self._load(track, play=True)

    def seek(self, milliseconds: int) -> None:
        self._player.setPosition(max(0, milliseconds))

    def set_volume(self, volume: float) -> None:
        self._output.setVolume(max(0.0, min(1.0, volume)))

    @property
    def volume(self) -> float:
        return self._output.volume()

    def toggle_mute(self) -> bool:
        self._output.setMuted(not self._output.isMuted())
        return self._output.isMuted()

    @property
    def muted(self) -> bool:
        return self._output.isMuted()

    # ── Modes ─────────────────────────────────────────────────────

    def toggle_shuffle(self) -> bool:
        state = self.queue.toggle_shuffle()
        self.queue_changed.emit()
        return state

    def cycle_repeat(self) -> Repeat:
        mode = self.queue.cycle_repeat()
        self.queue_changed.emit()
        return mode

    # ── Loading ───────────────────────────────────────────────────

    def _load(self, track: Optional[Track], *, play: bool) -> None:
        if track is None:
            self._player.stop()
            self.track_changed.emit(None)
            return

        self._loading = True
        self._counted = False
        self._player.setSource(QUrl.fromLocalFile(track.path))
        if play:
            self._player.play()

        self.track_changed.emit(track)
        self._loading = False

    # ── Qt signals ────────────────────────────────────────────────

    def _on_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and not self._loading:
            track = self.queue.next()
            if track is None:
                self.finished.emit()
                return
            # With repeat-one the "next" track is this one again, which needs an
            # explicit rewind — Qt will not replay a source it considers ended.
            self._load(track, play=True)

    def _on_state(self, _state) -> None:
        self.state_changed.emit(self.playing)

    def _on_position(self, position: int) -> None:
        duration = self.duration
        self._note_played(position, duration)

        if not self._throttle.isActive():
            self._throttle.start()
            self.position_changed.emit(position, duration)

    def _note_played(self, position: int, duration: int) -> None:
        if self._counted or self.track is None or duration <= 0:
            return
        played = position / duration
        if played >= PLAYED_FRACTION or position >= PLAYED_SECONDS * 1000:
            self._counted = True
            self.library.note_played(self.track)

    def _on_error(self, error, message: str = "") -> None:
        """A file that will not play should not stall the queue.

        Missing codec, deleted file, unreadable permissions — whatever the
        reason, the useful behaviour is to say so and move on, not to sit on a
        dead track with the play button lit.
        """
        if error == QMediaPlayer.Error.NoError:
            return

        track = self.track
        logger.warning("could not play %s: %s", track.path if track else "?", message)
        QTimer.singleShot(50, self.next)

    # ── State that outlives the process ───────────────────────────

    def save_state(self) -> dict:
        return {
            "queue": self.queue.to_dict(),
            "position": self.position,
            "volume": self.volume,
        }

    def restore_state(self, state: dict) -> None:
        """Put the queue back as it was, paused at the same spot."""
        if not isinstance(state, dict):
            return

        self.queue = Queue.from_dict(state.get("queue", {}), self.library.track)
        self.queue_changed.emit()

        try:
            self.set_volume(float(state.get("volume", 0.8)))
        except (TypeError, ValueError):
            pass

        track = self.queue.current
        if track is None:
            return

        # Loaded but not started: coming back to a program that immediately
        # makes noise is startling.
        self._load(track, play=False)
        try:
            position = int(state.get("position", 0))
        except (TypeError, ValueError):
            position = 0
        if position > 0:
            QTimer.singleShot(120, lambda: self._player.setPosition(position))
