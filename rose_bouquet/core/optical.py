"""Audio CDs — ripping them into the library, and burning them back out.

A music library that cannot read a shelf of CDs is missing the part of a
collection its owner actually paid for. So: read the disc, rip it into the same
folders everything else lives in, and write a playlist back to a blank when
somebody wants a disc for a car that only has a CD player.

Everything here is a thin wrapper around external tools. This module does not
implement CD reading — `cdparanoia` has two decades of drive-quirk and jitter
handling in it, and `libburn` the same for writing. What is added here is:
finding the drive, naming precisely which tool is missing and how to install it,
reporting progress the tools genuinely emitted, and handing the results to the
library like any other file.

**On the audio format for burning.** A Red Book audio CD holds one thing:
16-bit, 44.1kHz, stereo PCM. Anything else — a 48kHz FLAC, a mono recording, a
320kbps MP3 — has to be converted before it can be written, so every track goes
through ffmpeg first whether it looks like it needs it or not. Handing cdrskin
a file in the wrong format produces a disc of noise, and a disc of noise is a
coaster the user paid for.

**On progress.** Every percentage here came out of a tool's own output or off
the size of a file being written. Nothing is interpolated and nothing is timed;
a stage that cannot report progress reports `percent=None` rather than a number
that moves to look busy.

Nothing here touches the network.
"""

from __future__ import annotations

import logging
import re
import selectors
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

#: A CD sector is 1/75th of a second, and holds 588 stereo samples of 4 bytes.
FRAMES_PER_SECOND = 75
SAMPLES_PER_SECTOR = 588
BYTES_PER_SAMPLE = 4

#: cdparanoia counts its progress in 16-bit *words* — 588 stereo samples is
#: 1176 of them. Dividing by samples instead makes every track read as done
#: the moment it is half read, which is worse than showing nothing.
WORDS_PER_SECTOR = SAMPLES_PER_SECTOR * 2

#: What a disc can hold. 74 minutes is the guaranteed figure; most blanks take
#: 80. Anything past that is refused before a blank is wasted rather than
#: after.
STANDARD_MINUTES = 74
COMMON_MINUTES = 80

#: Red Book audio, which is the only thing an audio CD can contain.
SAMPLE_RATE = 44100
CHANNELS = 2


class DiscError(Exception):
    """Something went wrong with a disc, in terms a user can act on."""


class MissingToolError(DiscError):
    """A required external tool is not installed."""

    def __init__(self, tool: "ToolSpec") -> None:
        super().__init__(tool.message)
        self.tool = tool


class DiscCancelled(DiscError):
    """The user stopped it."""


# ── The tools this needs ──────────────────────────────────────────

@dataclass(frozen=True)
class ToolSpec:
    """One external program, and how to tell someone to install it."""

    binary: str
    purpose: str
    packages: dict[str, str] = field(default_factory=dict)

    @property
    def installed(self) -> bool:
        return shutil.which(self.binary) is not None

    @property
    def message(self) -> str:
        """What to tell the user, naming something they can actually install."""
        lines = [f"{self.binary} is needed to {self.purpose}, and is not installed."]
        if self.packages:
            lines.append("Install it with:")
            lines.extend(f"    {command}" for command in self.packages.values())
        return "\n".join(lines)


TOOLS: dict[str, ToolSpec] = {
    "cdparanoia": ToolSpec(
        binary="cdparanoia",
        purpose="read audio from a CD",
        packages={
            "arch": "pacman -S cdparanoia",
            "debian": "apt install cdparanoia",
            "fedora": "dnf install cdparanoia",
        },
    ),
    "cdrskin": ToolSpec(
        binary="cdrskin",
        purpose="write a CD",
        packages={
            "arch": "pacman -S libburn",
            "debian": "apt install cdrskin",
            "fedora": "dnf install cdrskin",
        },
    ),
    "ffmpeg": ToolSpec(
        binary="ffmpeg",
        purpose="convert audio to the format a CD requires",
        packages={
            "arch": "pacman -S ffmpeg",
            "debian": "apt install ffmpeg",
            "fedora": "dnf install ffmpeg",
        },
    ),
}


def require(name: str) -> str:
    """The path to a tool, or an error naming the package that provides it."""
    spec = TOOLS[name]
    path = shutil.which(spec.binary)
    if path is None:
        raise MissingToolError(spec)
    return path


def missing(*names: str) -> list[ToolSpec]:
    """Which of these tools are not installed. Empty means ready to go."""
    return [TOOLS[name] for name in names if not TOOLS[name].installed]


# ── Finding a drive ───────────────────────────────────────────────

@dataclass(frozen=True)
class Drive:
    """An optical drive."""

    device: Path
    name: str = ""
    can_write: bool = False

    @property
    def label(self) -> str:
        return f"{self.name} ({self.device})" if self.name else str(self.device)


#: Where the kernel lists optical drives, and the fallbacks for when it does
#: not — a drive behind USB sometimes only shows up as a device node.
CDROM_INFO = Path("/proc/sys/dev/cdrom/info")
DEVICE_GLOBS = ("/dev/sr[0-9]*", "/dev/cdrom*", "/dev/dvd*")


def parse_cdrom_info(text: str) -> list[Drive]:
    """Read the kernel's optical drive table.

    It is a transposed table: each row is a capability, and each column after
    the label is one drive. Read as rows-are-drives it silently reports one
    drive with nonsense capabilities, which is worse than reporting none.
    """
    names: list[str] = []
    writes: list[bool] = []

    for line in text.splitlines():
        if ":" not in line:
            continue
        label, _, rest = line.partition(":")
        values = rest.split()
        label = label.strip()

        if label == "drive name":
            names = values
        elif label == "Can write CD-R":
            writes = [value == "1" for value in values]

    drives = []
    for index, name in enumerate(names):
        can_write = writes[index] if index < len(writes) else False
        drives.append(Drive(device=Path("/dev") / name, can_write=can_write))
    return drives


def detect_drives() -> list[Drive]:
    """Every optical drive on the machine. Empty is normal, not an error."""
    drives: list[Drive] = []

    try:
        if CDROM_INFO.exists():
            drives = parse_cdrom_info(CDROM_INFO.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        logger.debug("could not read %s: %s", CDROM_INFO, exc)

    if drives:
        return [drive for drive in drives if drive.device.exists()]

    # No kernel table, or it listed nothing that exists: fall back to device
    # nodes, which is what a USB drive often shows up as.
    seen: set[Path] = set()
    for pattern in DEVICE_GLOBS:
        root = Path(pattern).parent
        for device in sorted(root.glob(Path(pattern).name)):
            resolved = device.resolve()
            if resolved not in seen:
                seen.add(resolved)
                drives.append(Drive(device=device, can_write=True))
    return drives


def default_drive() -> Optional[Drive]:
    drives = detect_drives()
    return drives[0] if drives else None


# ── What is on the disc ───────────────────────────────────────────

@dataclass(frozen=True)
class DiscTrack:
    """One track on an audio CD."""

    number: int
    frames: int
    title: str = ""
    artist: str = ""

    @property
    def seconds(self) -> float:
        return self.frames / FRAMES_PER_SECOND

    @property
    def clock(self) -> str:
        total = int(self.seconds)
        return f"{total // 60}:{total % 60:02d}"

    @property
    def display_title(self) -> str:
        return self.title or f"Track {self.number:02d}"


@dataclass(frozen=True)
class Disc:
    """An audio CD's table of contents."""

    tracks: tuple[DiscTrack, ...] = ()
    title: str = ""
    artist: str = ""

    @property
    def frames(self) -> int:
        return sum(track.frames for track in self.tracks)

    @property
    def seconds(self) -> float:
        return self.frames / FRAMES_PER_SECOND

    @property
    def clock(self) -> str:
        total = int(self.seconds)
        return f"{total // 60}:{total % 60:02d}"

    def __len__(self) -> int:
        return len(self.tracks)


#: cdparanoia -Q writes its TOC to stderr, one line per track:
#:     1.    18375 [04:05.00]        0 [00:00.00]    no   no  2
_TOC_LINE = re.compile(r"^\s*(\d+)\.\s+(\d+)\s+\[")


def parse_toc(text: str) -> Disc:
    """Read a `cdparanoia -Q` table of contents."""
    tracks = []
    for line in text.splitlines():
        match = _TOC_LINE.match(line)
        if match:
            number, frames = int(match.group(1)), int(match.group(2))
            tracks.append(DiscTrack(number=number, frames=frames))
    return Disc(tracks=tuple(tracks))


def read_toc(device: Optional[Path] = None) -> Disc:
    """Ask the drive what is on the disc.

    Raises `DiscError` when there is no readable audio disc, because every
    caller has to tell the difference between "no disc" and "a disc with no
    tracks" and an empty result cannot.
    """
    binary = require("cdparanoia")
    command = [binary, "-Q"]
    if device:
        command += ["-d", str(device)]

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiscError(f"Could not read the disc: {exc}") from exc

    # cdparanoia writes the TOC to stderr; the exit code is not a reliable
    # signal on its own, so what matters is whether any tracks were parsed.
    disc = parse_toc(result.stderr + result.stdout)
    if not disc.tracks:
        raise DiscError(
            "No audio CD in the drive.\n\n"
            "A data disc or a DVD will not appear here — this reads audio CDs."
        )
    return disc


# ── Progress ──────────────────────────────────────────────────────

@dataclass
class Progress:
    """How far along something is. `percent` is None when nothing honest
    can be computed rather than a number invented to look busy."""

    stage: str = ""
    message: str = ""
    percent: Optional[float] = None
    track: int = 0
    of_tracks: int = 0


ProgressCallback = Callable[[Progress], None]

#: cdparanoia's machine-readable progress, enabled by -e. The format string
#: "##: %d [%s] @ %ld" comes from cdparanoia release 10.2. The position is the
#: paranoia callback's sample index — 588 stereo samples per sector.
_CDPARANOIA = re.compile(r"^##:\s*(-?\d+)\s*\[([^\]]*)\]\s*@\s*(-?\d+)")


def parse_cdparanoia_progress(line: str, *, total_frames: Optional[int] = None) -> Optional[Progress]:
    """One cdparanoia `-e` progress line.

    A percentage needs the track length from the TOC: cdparanoia's line carries
    a position but never a total, so without one there is nothing to divide by.
    """
    match = _CDPARANOIA.match(line.strip())
    if not match:
        return None

    label, position = match.group(2), int(match.group(3))
    if position < 0:
        return Progress(stage="ripping", message=label)

    percent = None
    if total_frames:
        total_words = total_frames * WORDS_PER_SECTOR
        percent = min(100.0, position / total_words * 100) if total_words else None

    return Progress(stage="ripping", message=label, percent=percent)


#: cdrskin's burn status, e.g.
#:     12 of 700 MB written (fifo 100%) [buf  99%]   4.2x.
_CDRSKIN = re.compile(r"([\d.]+)\s+of\s+([\d.]+)\s+MB\s+written")


def parse_cdrskin_progress(line: str) -> Optional[Progress]:
    """One cdrskin burn status line.

    The percentage is the exact ratio of the two figures the tool printed, so
    it is unit-independent — which matters, because cdrecord's traditional
    "MB" is a MiB and that convention is not stated in the output.
    """
    match = _CDRSKIN.search(line)
    if not match:
        return None

    try:
        done, total = float(match.group(1)), float(match.group(2))
    except ValueError:
        return None

    percent = min(100.0, done / total * 100) if total else None
    return Progress(stage="burning", message=f"{done:.0f} of {total:.0f} MB", percent=percent)


# ── Running a tool ────────────────────────────────────────────────

class _Runner:
    """Runs an external tool, streaming its output and honouring cancellation.

    Output is read as bytes and split on both newline and carriage return: all
    of these tools redraw a status line in place with \\r, and a plain
    readline() would block until the tool finally emitted a newline — which for
    a forty-minute rip is at the very end.
    """

    POLL_SECONDS = 0.25
    TAIL_LINES = 30

    def __init__(self, cancel: Optional[threading.Event] = None) -> None:
        self._cancel = cancel or threading.Event()

    def run(self, command: Sequence[str], *,
            parser: Optional[Callable[[str], Optional[Progress]]] = None,
            progress: Optional[ProgressCallback] = None) -> tuple[int, list[str]]:
        logger.info("running: %s", " ".join(str(part) for part in command))

        try:
            process = subprocess.Popen(
                [str(part) for part in command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # Its own session, so cancelling reaches anything it forked.
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise DiscError(f"Could not run {command[0]}: not found.") from exc
        except PermissionError as exc:
            raise DiscError(
                f"Not permitted to run {command[0]}. Reading and writing discs "
                "usually needs your user to be in the 'optical' or 'cdrom' group."
            ) from exc

        tail: list[str] = []
        buffer = b""
        cancelled = False

        selector = selectors.DefaultSelector()
        assert process.stdout is not None
        selector.register(process.stdout, selectors.EVENT_READ)

        try:
            while True:
                if self._cancel.is_set() and not cancelled:
                    cancelled = True
                    process.terminate()

                if selector.select(timeout=self.POLL_SECONDS):
                    chunk = process.stdout.read1(4096)
                    if not chunk:
                        break
                    buffer, lines = _split_lines(buffer + chunk)
                    for line in lines:
                        tail.append(line)
                        del tail[:-self.TAIL_LINES]
                        if parser and progress:
                            update = parser(line)
                            if update is not None:
                                progress(update)
                elif process.poll() is not None:
                    break
        finally:
            selector.close()
            process.wait()

        if cancelled:
            raise DiscCancelled("Stopped.")

        return process.returncode, tail


def _split_lines(buffer: bytes) -> tuple[bytes, list[str]]:
    """Split on newline *and* carriage return, keeping any partial tail."""
    normalised = buffer.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    parts = normalised.split(b"\n")
    remainder = parts.pop()
    return remainder, [part.decode("utf-8", "replace") for part in parts if part.strip()]


def _failure(tool: str, code: int, tail: Sequence[str]) -> str:
    lines = [line for line in tail if line.strip()][-6:]
    detail = "\n".join(lines)
    return f"{tool} failed (exit {code})." + (f"\n\n{detail}" if detail else "")


# ── Ripping ───────────────────────────────────────────────────────

#: What a ripped track can be saved as. WAV is offered because it is what
#: cdparanoia already produces — choosing it skips the encode entirely.
RIP_FORMATS = ("flac", "mp3", "ogg", "wav")


@dataclass
class RipResult:
    """What came off the disc."""

    files: list[Path] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{len(self.files)} tracks ripped"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} could not be read")
        return ", ".join(parts)


def _safe(name: str) -> str:
    """A filename that survives every filesystem anyone will use."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return cleaned[:120] or "Track"


class CdRipper:
    """Reads an audio CD into files, one track at a time.

    Track by track rather than in one pass on purpose: a disc with one
    unreadable track should still yield the other eleven, and a rip that dies
    at track nine should leave the first eight where the user can play them.
    """

    def __init__(self, device: Optional[Path] = None,
                 cancel: Optional[threading.Event] = None) -> None:
        self.device = device
        self.cancel = cancel or threading.Event()
        self._runner = _Runner(self.cancel)

    def stop(self) -> None:
        self.cancel.set()

    def rip(
        self,
        disc: Disc,
        destination: Path,
        *,
        tracks: Optional[Iterable[int]] = None,
        fmt: str = "flac",
        album: str = "",
        artist: str = "",
        progress: Optional[ProgressCallback] = None,
        on_track: Optional[Callable[[int, Path], None]] = None,
    ) -> RipResult:
        """Rip `tracks` (default: all of them) into `destination`.

        `on_track` is called with each track as it finishes rather than only at
        the end, which is what lets a disc start playing on track one instead
        of after the whole disc has been read.
        """
        require("cdparanoia")
        if fmt != "wav":
            require("ffmpeg")

        wanted = list(tracks) if tracks is not None else [t.number for t in disc.tracks]
        chosen = [track for track in disc.tracks if track.number in wanted]
        if not chosen:
            raise DiscError("No tracks selected.")

        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)

        result = RipResult()
        for index, track in enumerate(chosen, start=1):
            if self.cancel.is_set():
                raise DiscCancelled("Stopped.")

            if progress:
                progress(Progress(
                    stage="ripping", track=index, of_tracks=len(chosen),
                    message=f"Reading {track.display_title}",
                ))

            raw = destination / f".rose-track-{track.number:02d}.wav"
            try:
                self._read_track(track, raw, index, len(chosen), progress)
            except DiscCancelled:
                raw.unlink(missing_ok=True)
                raise
            except DiscError as exc:
                # One bad track is not a failed disc.
                logger.warning("track %s could not be read: %s", track.number, exc)
                result.skipped.append(track.number)
                raw.unlink(missing_ok=True)
                continue

            name = f"{track.number:02d} - {_safe(track.display_title)}"
            final = destination / f"{name}.{fmt}"
            try:
                if fmt == "wav":
                    raw.replace(final)
                else:
                    self._encode(raw, final, track, album=album, artist=artist,
                                 index=index, total=len(chosen), progress=progress)
                    raw.unlink(missing_ok=True)
            except DiscCancelled:
                raw.unlink(missing_ok=True)
                raise
            except DiscError as exc:
                logger.warning("track %s could not be encoded: %s", track.number, exc)
                result.skipped.append(track.number)
                raw.unlink(missing_ok=True)
                continue

            result.files.append(final)
            if on_track is not None:
                try:
                    on_track(track.number, final)
                except Exception:       # noqa: BLE001 — a listener must not stop the rip
                    logger.exception("track callback failed")

        return result

    def _read_track(self, track: DiscTrack, target: Path, index: int,
                    total: int, progress: Optional[ProgressCallback]) -> None:
        command = [require("cdparanoia"), "-e"]
        if self.device:
            command += ["-d", str(self.device)]
        command += [str(track.number), str(target)]

        def parse(line: str) -> Optional[Progress]:
            update = parse_cdparanoia_progress(line, total_frames=track.frames)
            if update is not None:
                update.track, update.of_tracks = index, total
                update.message = f"{track.display_title} — {update.message}"
            return update

        code, tail = self._runner.run(command, parser=parse, progress=progress)
        if code != 0 or not target.exists():
            raise DiscError(_failure("cdparanoia", code, tail))

    def _encode(self, source: Path, target: Path, track: DiscTrack, *,
                album: str, artist: str, index: int, total: int,
                progress: Optional[ProgressCallback]) -> None:
        if progress:
            progress(Progress(stage="encoding", track=index, of_tracks=total,
                              message=f"Encoding {track.display_title}"))

        command = [
            require("ffmpeg"), "-nostdin", "-y", "-loglevel", "error",
            "-i", str(source),
            "-metadata", f"title={track.display_title}",
            "-metadata", f"track={track.number}",
        ]
        if album:
            command += ["-metadata", f"album={album}"]
        if artist:
            command += ["-metadata", f"artist={artist}", "-metadata", f"album_artist={artist}"]
        command.append(str(target))

        code, tail = self._runner.run(command)
        if code != 0 or not target.exists():
            raise DiscError(_failure("ffmpeg", code, tail))


# ── Burning ───────────────────────────────────────────────────────

@dataclass
class BurnResult:
    written: int = 0
    seconds: float = 0.0

    @property
    def summary(self) -> str:
        minutes = int(self.seconds // 60)
        return f"{self.written} tracks written ({minutes} minutes)"


def disc_minutes(seconds: float) -> float:
    return seconds / 60


def fits_on_a_disc(seconds: float, *, minutes: int = COMMON_MINUTES) -> bool:
    return disc_minutes(seconds) <= minutes


class CdBurner:
    """Writes audio files to a blank as a Red Book audio CD.

    Every track is converted to 16-bit 44.1kHz stereo first, without exception.
    Checking whether a file is "already right" and skipping the conversion
    would save a few seconds and risk a disc of noise, and the disc is the
    expensive part.
    """

    def __init__(self, device: Optional[Path] = None,
                 cancel: Optional[threading.Event] = None) -> None:
        self.device = device
        self.cancel = cancel or threading.Event()
        self._runner = _Runner(self.cancel)

    def stop(self) -> None:
        self.cancel.set()

    def burn(
        self,
        sources: Sequence[Path],
        *,
        workspace: Optional[Path] = None,
        speed: Optional[int] = None,
        simulate: bool = False,
        progress: Optional[ProgressCallback] = None,
    ) -> BurnResult:
        """Write these files, in this order, as audio tracks."""
        require("cdrskin")
        require("ffmpeg")

        files = [Path(path) for path in sources]
        if not files:
            raise DiscError("Nothing to burn.")

        absent = [path for path in files if not path.exists()]
        if absent:
            raise DiscError(
                "These are in the list but not on disk:\n" +
                "\n".join(f"  {path.name}" for path in absent[:6])
            )

        import tempfile

        workspace = Path(workspace) if workspace else Path(
            tempfile.mkdtemp(prefix="rose-bouquet-burn-"))
        workspace.mkdir(parents=True, exist_ok=True)

        prepared: list[Path] = []
        total_seconds = 0.0

        try:
            for index, source in enumerate(files, start=1):
                if self.cancel.is_set():
                    raise DiscCancelled("Stopped.")

                if progress:
                    progress(Progress(
                        stage="preparing", track=index, of_tracks=len(files),
                        message=f"Converting {source.name}",
                        percent=index / len(files) * 100,
                    ))

                target = workspace / f"{index:02d}.wav"
                self._to_red_book(source, target)
                prepared.append(target)
                total_seconds += _wav_seconds(target)

            if not fits_on_a_disc(total_seconds):
                raise DiscError(
                    f"That is {int(total_seconds // 60)} minutes of audio, and a CD "
                    f"holds about {COMMON_MINUTES}.\n\nRemove a few tracks and try again."
                )

            command = [require("cdrskin")]
            if self.device:
                command.append(f"dev={self.device}")
            if speed:
                command.append(f"speed={speed}")
            command += ["-v", "-audio", "-pad"]
            command.append("-dummy" if simulate else "-eject")
            command += [str(path) for path in prepared]

            code, tail = self._runner.run(
                command, parser=parse_cdrskin_progress, progress=progress)
            if code != 0:
                raise DiscError(_failure("cdrskin", code, tail))

            return BurnResult(written=len(prepared), seconds=total_seconds)

        finally:
            for path in prepared:
                path.unlink(missing_ok=True)

    def _to_red_book(self, source: Path, target: Path) -> None:
        command = [
            require("ffmpeg"), "-nostdin", "-y", "-loglevel", "error",
            "-i", str(source),
            "-ac", str(CHANNELS),
            "-ar", str(SAMPLE_RATE),
            "-sample_fmt", "s16",
            "-f", "wav",
            str(target),
        ]
        code, tail = self._runner.run(command)
        if code != 0 or not target.exists():
            raise DiscError(_failure("ffmpeg", code, tail))


def _wav_seconds(path: Path) -> float:
    """How long a WAV runs, read from its own header."""
    import wave

    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate() or SAMPLE_RATE
            return handle.getnframes() / rate
    except Exception as exc:            # noqa: BLE001 — a length is not worth raising over
        logger.debug("could not measure %s: %s", path, exc)
        return 0.0


# ── Video and data discs ──────────────────────────────────────────
#
# On DRM: there is no copy-protection circumvention here of any kind. Most
# commercial films are CSS-encrypted, and this module will not decrypt them —
# it copies the sectors a disc hands over. That is enough for an unencrypted
# disc (a home recording, a homebrew release, a disc you authored, most
# concert and documentary releases) and it is not enough for a Hollywood DVD,
# which will read as unreadable rather than as a broken file. Saying that
# plainly is better than a rip that silently produces noise.

VIDEO_TOOLS = {
    "dd": ToolSpec(
        binary="dd",
        purpose="copy a disc sector by sector",
        packages={"any": "part of coreutils, already on every Linux system"},
    ),
    "ddrescue": ToolSpec(
        binary="ddrescue",
        purpose="copy a disc that has read errors",
        packages={
            "arch": "pacman -S ddrescue",
            "debian": "apt install gddrescue",
            "fedora": "dnf install ddrescue",
        },
    ),
}
TOOLS.update(VIDEO_TOOLS)

#: A disc's contents, as far as this can tell without mounting it.
DISC_AUDIO = "audio"
DISC_DATA = "data"
DISC_NONE = "none"


def disc_kind(device: Optional[Path] = None) -> str:
    """What sort of disc is in the drive.

    Audio is detected by asking cdparanoia for a TOC, which is the only thing
    that reliably distinguishes an audio CD from a data one — a data disc has
    no audio tracks to report and says so.
    """
    try:
        if read_toc(device).tracks:
            return DISC_AUDIO
    except MissingToolError:
        raise
    except DiscError:
        pass

    target = Path(device) if device else None
    if target is not None and target.exists():
        try:
            # A data disc answers a read at sector zero; an empty tray does not.
            with open(target, "rb") as handle:
                handle.read(2048)
            return DISC_DATA
        except OSError:
            return DISC_NONE
    return DISC_NONE


@dataclass
class ImageResult:
    path: Optional[Path] = None
    bytes_written: int = 0

    @property
    def summary(self) -> str:
        megabytes = self.bytes_written / (1024 * 1024)
        return f"{self.path.name if self.path else 'image'} — {megabytes:.0f} MB"


class DiscImager(_Runner):
    """Copies a data or video disc to an image, and images back to a blank."""

    def __init__(self, device: Optional[Path] = None,
                 cancel: Optional[threading.Event] = None) -> None:
        super().__init__(cancel)
        self.device = device
        self.cancel = cancel or threading.Event()

    def stop(self) -> None:
        self.cancel.set()

    def rip_to_image(self, target: Path, *,
                     progress: Optional[ProgressCallback] = None) -> ImageResult:
        """Copy the disc in the drive to an .iso.

        `ddrescue` is preferred when it is installed because a scratched disc
        is the common case and it keeps going where `dd` stops dead; `dd` is
        the fallback because it is on every system.
        """
        if self.device is None:
            raise DiscError("No drive selected.")

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        if TOOLS["ddrescue"].installed:
            command = [require("ddrescue"), "-b", "2048", "-n",
                       str(self.device), str(target), str(target.with_suffix(".mapfile"))]
            parser = _parse_ddrescue_progress
        else:
            command = [require("dd"), f"if={self.device}", f"of={target}",
                       "bs=2048", "conv=noerror,sync", "status=progress"]
            parser = _parse_dd_progress

        code, tail = self.run(command, parser=parser, progress=progress)
        if code != 0 or not target.exists():
            raise DiscError(
                _failure(command[0], code, tail) +
                "\n\nA commercial film disc is encrypted and cannot be copied this way."
            )

        return ImageResult(path=target, bytes_written=target.stat().st_size)

    def burn_image(self, image: Path, *, speed: Optional[int] = None,
                   simulate: bool = False,
                   progress: Optional[ProgressCallback] = None) -> ImageResult:
        """Write an image the user already has to a blank."""
        image = Path(image)
        if not image.exists():
            raise DiscError(f"{image.name} is not there.")

        command = [require("cdrskin")]
        if self.device:
            command.append(f"dev={self.device}")
        if speed:
            command.append(f"speed={speed}")
        command += ["-v", "-dummy" if simulate else "-eject", str(image)]

        code, tail = self.run(command, parser=parse_cdrskin_progress, progress=progress)
        if code != 0:
            raise DiscError(_failure("cdrskin", code, tail))

        return ImageResult(path=image, bytes_written=image.stat().st_size)


#: ddrescue: "rescued: 123 MB, ... " on its status line.
_DDRESCUE = re.compile(r"rescued:\s*([\d.]+)\s*([kMGT]?B)", re.I)
#: dd's status=progress: "12345678 bytes (12 MB, 12 MiB) copied, ..."
_DD = re.compile(r"^(\d+)\s+bytes")

_UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}


def _parse_ddrescue_progress(line: str) -> Optional[Progress]:
    match = _DDRESCUE.search(line)
    if not match:
        return None
    try:
        amount = float(match.group(1)) * _UNITS.get(match.group(2).lower(), 1)
    except ValueError:
        return None
    # No total: the drive does not report a disc size that can be trusted
    # before the read finishes, so this reports what was copied and no percent.
    return Progress(stage="imaging", message=f"{amount / 1024 / 1024:.0f} MB read")


def _parse_dd_progress(line: str) -> Optional[Progress]:
    match = _DD.match(line.strip())
    if not match:
        return None
    amount = int(match.group(1))
    return Progress(stage="imaging", message=f"{amount / 1024 / 1024:.0f} MB read")
