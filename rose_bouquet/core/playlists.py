"""Playlists, stored as M3U files in a folder.

M3U rather than a database row, for the same reason notes are Markdown: a
playlist is a thing people move between programs, and every music player ever
written can read one. Rose Bouquet writes extended M3U, so the titles and
durations survive for anything that reads them and are ignored by anything that
does not.

Paths are written relative to the playlist folder when the music is underneath
it, and absolute otherwise. That way a music folder copied to another machine —
or to a phone — brings its playlists with it intact.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from rose_bouquet.core.library import Library, Track, data_dir

logger = logging.getLogger(__name__)

M3U_HEADER = "#EXTM3U"
_UNSAFE = re.compile(r"[^\w\- ]+")


def playlists_dir() -> Path:
    return data_dir() / "playlists"


def safe_name(title: str) -> str:
    """A filename for a playlist title, without inventing a path.

    Removing a character must not leave the gap it was in: "Songs / 2026"
    becomes "Songs 2026", not "Songs  2026" with a double space nobody typed.
    """
    cleaned = " ".join(_UNSAFE.sub("", title).split()) or "playlist"
    return cleaned[:80]


@dataclass
class Playlist:
    """A named, ordered list of tracks."""

    title: str = ""
    tracks: list[Track] = field(default_factory=list)
    path: Optional[Path] = None
    #: Where it came from, so an imported playlist can say so.
    source: str = "local"        # local | spotify | youtube
    #: Tracks the importer could not find. Kept so they can be shown and retried.
    missing: list[str] = field(default_factory=list)

    @property
    def duration(self) -> int:
        return sum(t.duration for t in self.tracks)

    @property
    def clock(self) -> str:
        seconds = self.duration
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        if hours:
            return f"{hours} hr {minutes} min"
        return f"{minutes} min"

    def add(self, tracks: Iterable[Track]) -> int:
        """Add tracks, skipping ones already in the playlist. Returns how many."""
        present = {t.path for t in self.tracks}
        added = 0
        for track in tracks:
            if track.path not in present:
                self.tracks.append(track)
                present.add(track.path)
                added += 1
        return added

    def remove_at(self, index: int) -> None:
        if 0 <= index < len(self.tracks):
            del self.tracks[index]

    def move(self, source: int, target: int) -> None:
        if 0 <= source < len(self.tracks) and 0 <= target < len(self.tracks):
            self.tracks.insert(target, self.tracks.pop(source))

    # ── Files ─────────────────────────────────────────────────────

    def to_m3u(self, folder: Optional[Path] = None) -> str:
        folder = Path(folder or playlists_dir())
        lines = [M3U_HEADER, f"#PLAYLIST:{self.title}"]

        if self.source != "local":
            lines.append(f"#ROSE-SOURCE:{self.source}")
        for missing in self.missing:
            # Kept as a comment: invisible to other players, still there when
            # Rose Bouquet reads it back and offers to find them again.
            lines.append(f"#ROSE-MISSING:{missing}")

        for track in self.tracks:
            lines.append(f"#EXTINF:{track.duration},{track.display_artist} - {track.display_title}")
            lines.append(_relative(Path(track.path), folder))

        return "\n".join(lines) + "\n"

    def save(self, folder: Optional[Path] = None) -> Path:
        folder = Path(folder or playlists_dir())
        folder.mkdir(parents=True, exist_ok=True)

        path = self.path or (folder / f"{safe_name(self.title)}.m3u")
        temporary = path.with_suffix(".part")
        temporary.write_text(self.to_m3u(folder), encoding="utf-8")
        temporary.replace(path)

        self.path = path
        return path

    def delete(self) -> None:
        if self.path is not None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    @classmethod
    def from_m3u(cls, path: Path, library: Library) -> "Playlist":
        """Read a playlist. Tracks not in the library are read off disk directly.

        A playlist pointing at a file the library has never seen is a normal
        thing — someone dropped an M3U in the folder — and it should still play.
        """
        from rose_bouquet.core.library import read_track

        path = Path(path)
        playlist = cls(title=path.stem, path=path)
        folder = path.parent

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.warning("could not read playlist %s: %s", path, exc)
            return playlist

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("#"):
                if line.startswith("#PLAYLIST:"):
                    playlist.title = line[len("#PLAYLIST:"):].strip() or playlist.title
                elif line.startswith("#ROSE-SOURCE:"):
                    playlist.source = line[len("#ROSE-SOURCE:"):].strip()
                elif line.startswith("#ROSE-MISSING:"):
                    playlist.missing.append(line[len("#ROSE-MISSING:"):].strip())
                continue

            target = Path(line)
            if not target.is_absolute():
                target = (folder / target).resolve()

            track = library.track(str(target))
            if track is None and target.exists():
                track = read_track(target)
            if track is not None:
                playlist.tracks.append(track)

        return playlist


def _relative(target: Path, folder: Path) -> str:
    try:
        return str(target.relative_to(folder))
    except ValueError:
        return str(target)


class PlaylistStore:
    """The playlist folder."""

    def __init__(self, folder: Optional[Path] = None) -> None:
        self.folder = Path(folder) if folder else playlists_dir()
        self.folder.mkdir(parents=True, exist_ok=True)

    def all(self, library: Library) -> list[Playlist]:
        playlists = [
            Playlist.from_m3u(path, library)
            for path in sorted(self.folder.glob("*.m3u"))
        ]
        return sorted(playlists, key=lambda p: p.title.lower())

    def create(self, title: str, tracks: Optional[Iterable[Track]] = None) -> Playlist:
        playlist = Playlist(title=title.strip() or "New playlist")
        if tracks:
            playlist.add(tracks)

        # Two playlists called the same thing must not overwrite each other.
        stem = safe_name(playlist.title)
        candidate = self.folder / f"{stem}.m3u"
        counter = 2
        while candidate.exists():
            candidate = self.folder / f"{stem}-{counter}.m3u"
            counter += 1

        playlist.path = candidate
        playlist.save(self.folder)
        return playlist

    def save(self, playlist: Playlist) -> Path:
        return playlist.save(self.folder)

    def delete(self, playlist: Playlist) -> None:
        playlist.delete()
