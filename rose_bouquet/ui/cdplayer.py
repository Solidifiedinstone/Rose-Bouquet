"""Playing an audio CD — actually playing it, not ripping it first.

The obvious implementation is to rip each track to a file and hand the file to
the media player. It is also wrong: pressing play on a CD should start the
music, not start a four-minute copy operation and fill a temporary folder with
audio nobody asked to keep.

So this streams. `cdparanoia` writes raw 16-bit 44.1kHz stereo samples to its
standard output, which is exactly what a `QAudioSink` consumes, so the drive
feeds the speakers with nothing in between and nothing on disk.

**Error correction is off while playing, and on while ripping.** Measured on
real hardware: full paranoia reads at 0.8x realtime — slower than the music
plays, so it could never keep up — and with `-Z` the same drive manages 4.5x.
For a rip you are keeping forever, the re-reads are worth it. For playback, a
drive that cannot keep ahead of the music is not a feature, and a rare glitch
on a scratched disc is a fair price for the disc playing at all.

Position comes from `QAudioSink.processedUSecs()`, which counts audio the
device has actually consumed rather than bytes read off the disc. Those differ
by however much is buffered ahead, and it is the played figure a progress bar
has to show.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from rose_bouquet.core import optical

logger = logging.getLogger(__name__)

#: How long to wait for the first audio before deciding something is wrong.
#: cdparanoia sits and waits indefinitely on a drive it cannot get at, so
#: without this the button looks broken and says nothing at all.
#:
#: Generous on purpose. Measured on a real drive with another reader competing
#: for it, the sink sat starved for nine to twelve seconds and then played
#: perfectly — so anything tighter reports a failure that is not one, which is
#: worse than the silence it was meant to explain.
FIRST_AUDIO_TIMEOUT_MS = 30000

#: Red Book audio: what every audio CD holds, and the only format needed here.
SAMPLE_RATE = 44100
CHANNELS = 2
BYTES_PER_FRAME = 4


def cd_format() -> QAudioFormat:
    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(CHANNELS)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return fmt


class CdPlayer(QObject):
    """Plays an audio CD, one track at a time, straight from the drive."""

    track_changed = Signal(object)        # DiscTrack | None
    state_changed = Signal(bool)          # playing?
    position_changed = Signal(int, int)   # position ms, duration ms
    finished = Signal()                   # the disc ran out
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.disc: Optional[optical.Disc] = None
        self.device = None
        self.index = -1

        self._sink: Optional[QAudioSink] = None
        self._process: Optional[QProcess] = None
        self._waited_ms = 0
        self._volume = 0.8
        self._paused = False

        # The sink reports its own progress; nothing else can be asked how far
        # through a track it is.
        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._tick)

    # ── What is playing ───────────────────────────────────────────

    @property
    def track(self) -> Optional[optical.DiscTrack]:
        if self.disc is None or not 0 <= self.index < len(self.disc.tracks):
            return None
        return self.disc.tracks[self.index]

    @property
    def playing(self) -> bool:
        return self._sink is not None and not self._paused

    @property
    def position(self) -> int:
        """Milliseconds into the current track."""
        if self._sink is None:
            return 0
        return int(self._sink.processedUSecs() / 1000)

    @property
    def duration(self) -> int:
        track = self.track
        return int(track.seconds * 1000) if track else 0

    # ── Controls ──────────────────────────────────────────────────

    def play_disc(self, disc: optical.Disc, device=None, start: int = 0) -> None:
        self.disc = disc
        self.device = device
        self.index = max(0, min(start, len(disc.tracks) - 1))
        self._start_current()

    def stop(self) -> None:
        self._ticker.stop()
        self._teardown()
        self.index = -1
        self.track_changed.emit(None)
        self.state_changed.emit(False)

    def toggle(self) -> None:
        logger.info("cd: toggle, sink=%s paused=%s", self._sink, self._paused)
        if self._sink is None:
            return
        if self._paused:
            self._sink.resume()
            self._paused = False
        else:
            self._sink.suspend()
            self._paused = True
        self.state_changed.emit(self.playing)

    def next(self) -> None:
        if self.disc is None:
            return
        if self.index + 1 >= len(self.disc.tracks):
            self.stop()
            self.finished.emit()
            return
        self.index += 1
        self._start_current()

    def previous(self) -> None:
        """Back a track, or to the start of this one if it is under way."""
        if self.disc is None:
            return
        if self.position > 3000 or self.index == 0:
            self._start_current()
            return
        self.index -= 1
        self._start_current()

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))
        if self._sink is not None:
            self._sink.setVolume(self._volume)

    # ── Running one track ─────────────────────────────────────────

    def _start_current(self) -> None:
        track = self.track
        if track is None:
            return

        self._teardown()

        fmt = cd_format()
        if not QMediaDevices.defaultAudioOutput().isFormatSupported(fmt):
            self.failed.emit("This machine will not play CD audio directly.")
            return

        process = QProcess(self)
        arguments = ["-e", "-Z", "-r"]
        if self.device:
            arguments += ["-d", str(self.device)]
        arguments += [str(track.number), "-"]

        # cdparanoia's chatter goes to stderr and must not reach the audio.
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        # Started without waiting on it. `waitForStarted` blocks the interface
        # for as long as it takes, which on a drive that is still spinning up
        # is seconds of a frozen window — and a failure to start is reported
        # by the signal below just as well, without the freeze.
        process.errorOccurred.connect(self._process_failed)
        process.start("cdparanoia", arguments)

        sink = QAudioSink(fmt, self)
        sink.setVolume(self._volume)
        sink.start(process)
        logger.info("cd: track %s, cdparanoia %s, volume %.2f, sink %s",
                    track.number, " ".join(arguments), self._volume, sink.state())

        self._process = process
        self._sink = sink
        self._paused = False
        self._waited_ms = 0

        self._ticker.start()
        self.track_changed.emit(track)
        self.state_changed.emit(True)

    def _teardown(self) -> None:
        """Drop the current reader and sink. Never blocks.

        This runs on every track change, so waiting for the old cdparanoia to
        die would stutter the interface each time somebody presses skip. It is
        killed and let go; Qt reaps it when it goes, and its signals are
        disconnected first so a dying process cannot report a failure that
        belongs to the track before this one.
        """
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None

        if self._process is not None:
            process, self._process = self._process, None
            try:
                process.errorOccurred.disconnect(self._process_failed)
            except (RuntimeError, TypeError):
                pass
            process.kill()
            process.deleteLater()

        self._paused = False

    def _process_failed(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.failed.emit(
                "Could not start cdparanoia — is it installed?")
            self.stop()

    def _tick(self) -> None:
        if self._sink is None:
            return

        self.position_changed.emit(self.position, self.duration)
        logger.debug("cd: sink=%s processed=%dms free=%d/%d reader=%s",
                     self._sink.state(), self.position, self._sink.bytesFree(),
                     self._sink.bufferSize(),
                     self._process.state() if self._process else None)

        # Nothing yet? Give the drive a while, then say so rather than sitting
        # silent. cdparanoia waits indefinitely on a busy drive, so waiting for
        # it to fail is waiting forever.
        #
        # The signal is the sink's own state, measured against this drive: a
        # sink being fed reads Active, and one that is starved reads Idle with
        # nothing processed. Watching the position alone cannot tell the
        # difference between "not started yet" and "playing quietly".
        starved = (self._sink.state() == QAudio.State.IdleState
                   and self._sink.processedUSecs() == 0)

        if starved and not self._paused:
            self._waited_ms += self._ticker.interval()
            if self._waited_ms >= FIRST_AUDIO_TIMEOUT_MS:
                self.failed.emit(self._why_nothing_played())
                self.stop()
            return

        self._waited_ms = 0

        # A track ends when cdparanoia has exited *and* the sink has run dry.
        # Both conditions matter: the sink keeps playing what it buffered for a
        # second or two after the reader finishes, and stopping at the reader's
        # exit would clip the end off every track.
        reader_done = (self._process is None
                       or self._process.state() == QProcess.ProcessState.NotRunning)
        drained = self._sink.bytesFree() >= self._sink.bufferSize()

        if not (reader_done and drained):
            return

        # A track that produced no audio at all did not *finish*, it failed —
        # the drive was busy, the disc was pulled, cdparanoia was not allowed
        # to read it. Advancing quietly here walks the whole disc in a second
        # and stops, which looks exactly like the button doing nothing.
        if self.position < 500:
            self.failed.emit(self._why_nothing_played())
            self.stop()
            return

        self.next()

    def _why_nothing_played(self) -> str:
        """cdparanoia's own complaint, if it made one."""
        detail = ""
        if self._process is not None:
            raw = bytes(self._process.readAllStandardError()).decode("utf-8", "replace")
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            # The last line is the actual complaint; the rest is a banner.
            detail = lines[-1] if lines else ""

        if "busy" in detail.lower() or "device or resource" in detail.lower():
            return "The drive is busy — something else is reading the disc."
        return detail or "Nothing came off the disc. Is it still in the drive?"
