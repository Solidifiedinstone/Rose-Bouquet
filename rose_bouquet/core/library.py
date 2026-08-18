"""The music on disk, and what is known about it.

A track is a file plus its tags. The file is the truth: the library is a cache
of what was found last time it looked, and anything in it can be rebuilt by
scanning again. That is deliberate — a music library that disagrees with the
filesystem and cannot be told to look again is a library people abandon.

Tags are read with mutagen, which handles MP3, FLAC, Ogg, Opus, M4A and WAV
through one interface. A file whose tags cannot be read is still added, named
after its filename: a track you can play with a bad name beats a track that
silently vanished.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

logger = logging.getLogger(__name__)

#: Everything mutagen can read that anyone actually keeps music in.
AUDIO_SUFFIXES = {
    ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".mp4", ".aac",
    ".wav", ".wma", ".alac", ".aiff", ".ape", ".wv",
}

#: Cover art files sitting next to the music, in the order they are preferred.
COVER_NAMES = ("cover", "folder", "front", "album", "albumart")
COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

#: What an album gets called when its tracks credit different artists.
VARIOUS_ARTISTS = "Various Artists"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "rose-bouquet"


def music_dir() -> Path:
    """The user's music folder, honouring XDG_MUSIC_DIR if it is set."""
    configured = os.environ.get("XDG_MUSIC_DIR")
    if configured:
        return Path(configured)

    # xdg-user-dirs writes this file; parsing it is cheaper than shelling out.
    config = Path.home() / ".config" / "user-dirs.dirs"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            if line.startswith("XDG_MUSIC_DIR"):
                value = line.split("=", 1)[1].strip().strip('"')
                return Path(value.replace("$HOME", str(Path.home())))
    except OSError:
        pass

    return Path.home() / "Music"


@dataclass
class Track:
    """One audio file, and the tags read out of it."""

    path: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    #: Seconds. 0 means "not known yet" rather than "instantaneous".
    duration: int = 0
    track_number: int = 0
    disc_number: int = 0
    year: str = ""
    genre: str = ""

    #: Where it came from, for anything downloaded rather than ripped.
    source: str = "local"        # local | youtube
    source_id: str = ""          # the YouTube video id, when there is one

    added: str = ""
    play_count: int = 0
    last_played: str = ""
    #: Cover art next to the file, if any was found.
    cover: str = ""

    # ── Display ───────────────────────────────────────────────────

    @property
    def file(self) -> Path:
        return Path(self.path)

    @property
    def exists(self) -> bool:
        return self.file.exists()

    @property
    def display_title(self) -> str:
        return self.title or self.file.stem

    @property
    def display_artist(self) -> str:
        return self.artist or "Unknown artist"

    @property
    def display_album(self) -> str:
        return self.album or "Unknown album"

    @property
    def clock(self) -> str:
        """Duration as m:ss, or h:mm:ss for anything over an hour."""
        seconds = max(0, int(self.duration))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @property
    def search_text(self) -> str:
        return " ".join((self.title, self.artist, self.album, self.genre)).lower()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in fields})


# ── Reading tags ──────────────────────────────────────────────────

def _first(tags, *names: str) -> str:
    """The first tag present out of several spellings of the same thing."""
    for name in names:
        try:
            value = tags.get(name)
        except (AttributeError, TypeError):
            continue
        if not value:
            continue
        if isinstance(value, list):
            value = value[0] if value else ""
        text = str(value).strip()
        if text:
            return text
    return ""


def _number(text: str) -> int:
    """"3", "3/12" and "03" all mean 3. Anything else means 0."""
    head = str(text).split("/")[0].strip()
    try:
        return int(head)
    except (TypeError, ValueError):
        return 0


def find_cover(path: Path) -> str:
    """Cover art sitting beside the file, if there is any."""
    folder = path.parent
    for name in COVER_NAMES:
        for suffix in COVER_SUFFIXES:
            candidate = folder / f"{name}{suffix}"
            if candidate.exists():
                return str(candidate)
    return ""


def read_track(path: Path) -> Track:
    """Build a track from a file. Never raises — a bad file is still a track."""
    path = Path(path)
    track = Track(
        path=str(path),
        added=datetime.now().isoformat(timespec="seconds"),
        cover=find_cover(path),
    )

    try:
        import mutagen

        audio = mutagen.File(path, easy=True)
    except Exception as exc:                      # noqa: BLE001 — any tag error
        logger.debug("no tags for %s: %s", path, exc)
        audio = None

    if audio is None:
        track.title = path.stem
        return track

    tags = getattr(audio, "tags", None) or {}
    track.title = _first(tags, "title") or path.stem
    track.artist = _first(tags, "artist", "performer")
    track.album = _first(tags, "album")
    track.album_artist = _first(tags, "albumartist") or track.artist
    track.year = _first(tags, "date", "year", "originaldate")[:4]
    track.genre = _first(tags, "genre")
    track.track_number = _number(_first(tags, "tracknumber"))
    track.disc_number = _number(_first(tags, "discnumber"))

    info = getattr(audio, "info", None)
    if info is not None and getattr(info, "length", 0):
        track.duration = int(info.length)

    return track


def scan(folders: Iterable[Path]) -> Iterator[Path]:
    """Every audio file under a set of folders, deepest paths included.

    Yields rather than returning a list so a scan of a large library can report
    progress and stay responsive instead of freezing until it is done.
    """
    for folder in folders:
        folder = Path(folder).expanduser()
        if not folder.is_dir():
            continue
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                if Path(name).suffix.lower() in AUDIO_SUFFIXES:
                    yield Path(root) / name


def _inside(folder: Path, path: str) -> bool:
    """Whether `path` names a file under `folder`.

    Compared as text: the folder is missing, so nothing about it can be
    resolved on disk, and asking the filesystem would answer about the
    mountpoint standing in for it rather than about the folder itself.
    """
    prefix = str(folder).rstrip(os.sep) + os.sep
    return path.startswith(prefix)


# ── The library ───────────────────────────────────────────────────

@dataclass
class Library:
    """Every known track, indexed by path."""

    path: Optional[Path] = None
    tracks: dict[str, Track] = field(default_factory=dict)
    #: Folders that get scanned. Empty means "the user's music folder".
    folders: list[str] = field(default_factory=list)

    # ── Scanning ──────────────────────────────────────────────────

    def roots(self) -> list[Path]:
        return [Path(f).expanduser() for f in self.folders] or [music_dir()]

    def missing_roots(self) -> list[Path]:
        """Configured folders that are not there right now.

        A drive that did not come up, a path that was renamed, a share that is
        not reachable — from here they all look the same, and they all mean the
        same thing: what is under this folder is unknown, not gone.
        """
        return [root for root in self.roots() if not root.is_dir()]

    def rescan(self, *, progress=None) -> tuple[int, int]:
        """Bring the library in step with the disk. Returns (added, removed).

        Files already known are not re-read unless they have changed on disk,
        because reading tags is the slow part and most of a library does not
        change between scans.
        """
        added = 0
        seen: set[str] = set()
        absent = self.missing_roots()

        for index, file in enumerate(scan(self.roots())):
            key = str(file)
            seen.add(key)

            existing = self.tracks.get(key)
            if existing is None:
                self.tracks[key] = read_track(file)
                added += 1
            elif not existing.duration:
                # A track added before its tags could be read gets another go.
                self.tracks[key] = read_track(file)

            if progress is not None and index % 25 == 0:
                progress(index, key)

        # Anything gone from disk goes from the library. Play counts go with it,
        # which is the honest outcome: the file is not there any more.
        #
        # Except when the folder it lived in is the thing that is missing. An
        # unmounted drive walks exactly like a library someone emptied, and
        # treating the two alike would delete a thousand tracks and every play
        # count on them the first time a disk came up late or a name moved
        # from one disk to another. A folder we cannot read is unknown, not
        # empty, and its tracks are left where they are until it comes back.
        removed = 0
        for key in list(self.tracks):
            if key in seen or self.tracks[key].source != "local":
                continue
            if any(_inside(root, key) for root in absent):
                continue
            del self.tracks[key]
            removed += 1

        return added, removed

    def add(self, track: Track) -> Track:
        self.tracks[track.path] = track
        return track

    def remove(self, track: Track) -> None:
        self.tracks.pop(track.path, None)

    # ── Queries ───────────────────────────────────────────────────

    def all(self) -> list[Track]:
        return sorted(
            self.tracks.values(),
            key=lambda t: (t.display_artist.lower(), t.display_album.lower(),
                           t.disc_number, t.track_number, t.display_title.lower()),
        )

    def search(self, query: str) -> list[Track]:
        query = query.strip().lower()
        if not query:
            return self.all()
        return [t for t in self.all() if query in t.search_text]

    def albums(self) -> dict[tuple[str, str], list[Track]]:
        """(album artist, album) → its tracks, in track order.

        Grouped by album first, then attributed: an album whose tracks credit
        different artists and carry no album-artist tag is one compilation by
        "Various Artists", not one album per artist. Splitting a soundtrack into
        thirty one-track albums is the classic music-library bug.
        """
        by_album: dict[str, list[Track]] = {}
        for track in self.tracks.values():
            by_album.setdefault(track.display_album, []).append(track)

        grouped: dict[tuple[str, str], list[Track]] = {}
        for album, tracks in by_album.items():
            tagged = {t.album_artist for t in tracks if t.album_artist}
            if len(tagged) == 1:
                artist = next(iter(tagged))
            else:
                performers = {t.display_artist for t in tracks}
                artist = next(iter(performers)) if len(performers) == 1 else VARIOUS_ARTISTS

            tracks.sort(key=lambda t: (t.disc_number, t.track_number, t.display_title))
            grouped[(artist, album)] = tracks

        return dict(sorted(grouped.items(), key=lambda pair: (pair[0][0].lower(), pair[0][1].lower())))

    def artists(self) -> dict[str, list[Track]]:
        grouped: dict[str, list[Track]] = {}
        for track in self.tracks.values():
            grouped.setdefault(track.album_artist or track.display_artist, []).append(track)
        return dict(sorted(grouped.items(), key=lambda pair: pair[0].lower()))

    def recently_added(self, limit: int = 40) -> list[Track]:
        return sorted(self.tracks.values(), key=lambda t: t.added, reverse=True)[:limit]

    def most_played(self, limit: int = 40) -> list[Track]:
        played = [t for t in self.tracks.values() if t.play_count]
        return sorted(played, key=lambda t: t.play_count, reverse=True)[:limit]

    def track(self, path: str) -> Optional[Track]:
        return self.tracks.get(path)

    def note_played(self, track: Track) -> None:
        track.play_count += 1
        track.last_played = datetime.now().isoformat(timespec="seconds")

    # ── Persistence ───────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "folders": list(self.folders),
            "tracks": [t.to_dict() for t in self.tracks.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Library":
        library = cls()
        folders = data.get("folders")
        if isinstance(folders, list):
            library.folders = [str(f) for f in folders]

        rows = data.get("tracks")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    track = Track.from_dict(row)
                except (TypeError, ValueError):
                    continue
                if track.path:
                    library.tracks[track.path] = track

        return library

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Library":
        path = Path(path) if path else data_dir() / "library.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            library = cls()
        except (OSError, ValueError) as exc:
            logger.error("could not read %s: %s", path, exc)
            library = cls()
        else:
            library = cls.from_dict(data) if isinstance(data, dict) else cls()

        library.path = path
        return library

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path) if path else (self.path or data_dir() / "library.json")
        self.path = path

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".part")
            temporary.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            logger.error("could not save library to %s: %s", path, exc)
