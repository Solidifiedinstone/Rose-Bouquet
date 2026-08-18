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
from pathlib import Path
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

#: How many unplayable tracks in a row before the queue stops trying. One dead
#: file is worth stepping over; a whole run of them means the music itself is
#: absent — an unmounted drive, a folder moved — and racing on through the
#: library only reports the same problem another thousand times.
FAILURE_LIMIT = 3


class Playback(QObject):
    """Plays the queue."""

    track_changed = Signal(object)      # Track | None
    state_changed = Signal(bool)        # playing?
    position_changed = Signal(int, int)  # position ms, duration ms
    queue_changed = Signal()
    finished = Signal()                 # the queue ran out
    #: Playback gave up, with a sentence saying why. Distinct from `finished`:
    #: the queue did not end, it stopped being playable.
    failed = Signal(str)
    volume_changed = Signal(float)      # 0.0 – 1.0
    #: A seek the user asked for, in ms. Distinct from `position_changed`,
    #: which also fires as a track plays normally: desktop media controls need
    #: to know when the position *jumped*, not when it advanced.
    seeked = Signal(int)

    def __init__(self, library: Library, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.library = library
        self.queue = Queue()

        self._output = QAudioOutput()
        self._output.setVolume(0.8)
        # Relayed through a lambda rather than connected signal-to-signal:
        # Qt's own volumeChanged carries a C++ float, which will not bind
        # directly to a Python-declared Signal(float).
        self._output.volumeChanged.connect(lambda value: self.volume_changed.emit(float(value)))

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
        #: Unplayable tracks since the last one that opened.
        self._failures = 0

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
        position = max(0, milliseconds)
        self._player.setPosition(position)
        self.seeked.emit(position)

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

    def set_shuffle(self, shuffle: bool) -> None:
        """Set shuffle outright. Toggling is what a button wants; a desktop
        media control sends the state it wants and expects that state."""
        self.queue.set_shuffle(bool(shuffle))
        self.queue_changed.emit()

    def set_repeat(self, mode: Repeat) -> None:
        self.queue.repeat = mode
        self.queue_changed.emit()

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
        # A file that opened is proof the music is reachable again, so the run
        # of failures that came before it no longer counts against the queue.
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            self._failures = 0

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
        """A file that will not play should not stall the queue — or run away with it.

        Missing codec, deleted file, unreadable permissions — whatever the
        reason, the useful behaviour is to say so and move on, not to sit on a
        dead track with the play button lit. But moving on has to stop
        somewhere: when the drive holding the library is not mounted, *every*
        track fails, and skipping each one in turn tears through the whole
        library at a few tracks a second, saying nothing useful while it goes.
        """
        if error == QMediaPlayer.Error.NoError:
            return

        track = self.track
        logger.warning("could not play %s: %s", track.path if track else "?", message)

        self._failures += 1
        if self._failures >= FAILURE_LIMIT:
            self._give_up(track)
            return

        QTimer.singleShot(50, self.next)

    def _give_up(self, track: Optional[Track]) -> None:
        """Stop, and say what is actually wrong rather than naming a file."""
        self._player.stop()
        self._failures = 0
        logger.warning("stopped after %d unplayable tracks in a row", FAILURE_LIMIT)
        self.failed.emit(self._diagnosis(track))

    @staticmethod
    def _diagnosis(track: Optional[Track]) -> str:
        if track is None:
            return "Nothing in the queue would play."

        missing = Playback._missing_folder(track.path)
        if missing is not None:
            return f"{missing} is not there — is the drive mounted?"
        return "Several tracks in a row would not play, so playback stopped."

    @staticmethod
    def _missing_folder(path: str) -> Optional[str]:
        """The outermost folder on this track's path that has gone missing.

        Naming the file is no help when an entire drive is absent; naming the
        folder that vanished is the part someone can act on.
        """
        candidate = Path(path)
        if candidate.exists():
            return None

        missing = None
        for parent in candidate.parents:
            if parent.exists():
                break
            missing = parent
        return str(missing) if missing is not None else None

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
