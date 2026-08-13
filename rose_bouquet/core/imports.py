"""An import that survives being interrupted.

Importing a playlist is a long job made of many small ones: read the track list,
find each track, download each match. Any of those can be cut short — the window
closes, the network drops, YouTube refuses one video, the machine sleeps. Before
this, that meant starting over, and starting over meant re-downloading what you
already had.

So an import is a *record on disk* rather than a run of a function. Every track
is a row with a state, the record is written as each row changes, and resuming
is just "do the rows that are not done yet". Two useful properties fall out:

  - **Nothing is downloaded twice.** A row already `done`, or whose video is
    already in the library, is skipped.
  - **A part-read playlist can be topped up.** If only the first hundred tracks
    could be read, importing the same playlist again adds the rest to the same
    record instead of making a second one.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

PENDING = "pending"
MATCHED = "matched"
DONE = "done"
MISSING = "missing"
FAILED = "failed"

_UNSAFE = re.compile(r"[^\w\-]+")


def imports_dir() -> Path:
    from rose_bouquet.core.library import data_dir

    return data_dir() / "imports"


@dataclass
class Entry:
    """One track of a playlist, and how far it has got."""

    title: str = ""
    artist: str = ""
    #: The YouTube video it matched to, once it has been looked up.
    video_id: str = ""
    #: pending | matched | done | missing | failed
    state: str = PENDING
    #: Where the file ended up, for anything downloaded.
    path: str = ""
    error: str = ""

    @property
    def key(self) -> str:
        """Identity within a playlist, so re-imports line up with old rows."""
        return f"{self.artist.lower().strip()}|{self.title.lower().strip()}"

    def __str__(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class ImportJob:
    """A playlist being imported, and how far through it is."""

    title: str = ""
    link: str = ""
    entries: list[Entry] = field(default_factory=list)
    started: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    #: True when the source could not be read in full — so a later run knows
    #: there may be more to fetch rather than assuming it is complete.
    partial: bool = False
    #: Where to carry on reading the source from. None means it was read to the
    #: end. This is what turns "it only got 100" into "it will get the rest".
    next_offset: Optional[int] = None
    #: How many tracks the source says the playlist has.
    expected_total: int = 0
    #: When Spotify will accept requests again, if it has told us to wait.
    #: Written down so the app can say "in about six hours" rather than
    #: failing repeatedly and looking broken.
    blocked_until: str = ""
    path: Optional[Path] = None

    # ── Progress ──────────────────────────────────────────────────

    def count(self, state: str) -> int:
        return sum(1 for entry in self.entries if entry.state == state)

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def finished(self) -> bool:
        """Nothing left to read, match, or download."""
        if not self.fully_read:
            return False
        return not any(e.state in (PENDING, MATCHED) for e in self.entries)

    def wait_remaining(self, now: Optional[datetime] = None) -> int:
        """Seconds until the source will talk to us again. 0 when it will."""
        if not self.blocked_until:
            return 0
        try:
            until = datetime.fromisoformat(self.blocked_until)
        except ValueError:
            return 0
        return max(0, int((until - (now or datetime.now())).total_seconds()))

    def block_for(self, seconds: int) -> None:
        from datetime import timedelta

        self.blocked_until = (datetime.now() + timedelta(seconds=max(0, seconds))).isoformat(
            timespec="seconds")

    @property
    def fully_read(self) -> bool:
        """Whether the whole playlist has been read from the source yet."""
        return self.next_offset is None

    @property
    def summary(self) -> str:
        done, missing, failed = self.count(DONE), self.count(MISSING), self.count(FAILED)
        left = self.total - done - missing - failed

        parts = [f"{done} of {self.total} downloaded"]

        if not self.fully_read:
            remaining = max(0, self.expected_total - self.total) if self.expected_total else 0
            parts.insert(0, f"{remaining} still to read from Spotify"
                         if remaining else "more still to read from Spotify")

        # The thing stopping progress goes first, because it is the thing the
        # user needs to know.
        wait = self.wait_remaining()
        if wait:
            parts.insert(0, f"Spotify is rate-limiting this connection for another {_plainly(wait)}")
        if left:
            parts.append(f"{left} to go")
        if missing:
            parts.append(f"{missing} not found")
        if failed:
            parts.append(f"{failed} failed")
        return " · ".join(parts)

    def pending(self) -> list[Entry]:
        """Everything still to be downloaded, in playlist order."""
        return [e for e in self.entries if e.state == MATCHED and e.video_id]

    def unmatched(self) -> list[Entry]:
        return [e for e in self.entries if e.state == PENDING]

    def missing(self) -> list[Entry]:
        return [e for e in self.entries if e.state == MISSING]

    # ── Building and updating ─────────────────────────────────────

    def add_tracks(self, tracks: Iterable) -> int:
        """Add rows for tracks not already recorded. Returns how many were new.

        This is what makes a second pass at a part-read playlist top up the same
        record: rows are keyed by artist and title, so the hundred already there
        are recognised and only the rest are added.
        """
        known = {entry.key for entry in self.entries}
        added = 0

        for track in tracks:
            entry = Entry(title=getattr(track, "title", ""), artist=getattr(track, "artist", ""))
            if entry.key in known:
                continue
            known.add(entry.key)
            self.entries.append(entry)
            added += 1

        return added

    def note_match(self, entry: Entry, video_id: str) -> None:
        entry.video_id = video_id
        entry.state = MATCHED if video_id else MISSING

    def note_done(self, video_id: str, path: str) -> Optional[Entry]:
        for entry in self.entries:
            if entry.video_id == video_id and entry.state != DONE:
                entry.state = DONE
                entry.path = path
                return entry
        return None

    def note_failed(self, video_id: str, error: str) -> Optional[Entry]:
        for entry in self.entries:
            if entry.video_id == video_id and entry.state == MATCHED:
                entry.state = FAILED
                entry.error = error[:300]
                return entry
        return None

    def skip_already_downloaded(self, library) -> int:
        """Mark rows whose audio is already in the library. Returns how many.

        Checked by YouTube id first, then by artist and title, so a track ripped
        from a CD counts as already had — the point is to end up with the music,
        not to collect duplicates of it.
        """
        by_source = {t.source_id: t for t in library.tracks.values() if t.source_id}
        by_name = {
            f"{t.display_artist.lower().strip()}|{t.display_title.lower().strip()}": t
            for t in library.tracks.values()
        }

        skipped = 0
        for entry in self.entries:
            if entry.state == DONE:
                continue
            track = by_source.get(entry.video_id) if entry.video_id else None
            track = track or by_name.get(entry.key)
            if track is not None:
                entry.state = DONE
                entry.path = track.path
                skipped += 1

        return skipped

    # ── Persistence ───────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "title": self.title,
            "link": self.link,
            "started": self.started,
            "partial": self.partial,
            "next_offset": self.next_offset,
            "expected_total": self.expected_total,
            "blocked_until": self.blocked_until,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImportJob":
        job = cls(
            title=str(data.get("title") or ""),
            link=str(data.get("link") or ""),
            started=str(data.get("started") or ""),
            partial=bool(data.get("partial")),
        )
        job.blocked_until = str(data.get("blocked_until") or "")
        offset = data.get("next_offset")
        job.next_offset = int(offset) if isinstance(offset, int) else None
        try:
            job.expected_total = int(data.get("expected_total") or 0)
        except (TypeError, ValueError):
            job.expected_total = 0
        rows = data.get("entries")
        if isinstance(rows, list):
            job.entries = [Entry.from_dict(r) for r in rows if isinstance(r, dict)]
        return job

    def save(self, folder: Optional[Path] = None) -> Path:
        folder = Path(folder or imports_dir())
        folder.mkdir(parents=True, exist_ok=True)

        if self.path is None:
            stem = _UNSAFE.sub("-", self.title).strip("-").lower() or "import"
            self.path = folder / f"{stem}.json"

        try:
            temporary = self.path.with_suffix(".part")
            temporary.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            logger.error("could not save the import record: %s", exc)

        return self.path

    @classmethod
    def load(cls, path: Path) -> Optional["ImportJob"]:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("could not read import %s: %s", path, exc)
            return None

        if not isinstance(data, dict):
            return None
        job = cls.from_dict(data)
        job.path = Path(path)
        return job

    @classmethod
    def for_link(cls, link: str, title: str, folder: Optional[Path] = None) -> "ImportJob":
        """The record for a playlist — the existing one if there is one.

        Importing the same playlist twice continues it rather than starting a
        second copy, which is what makes "read the first hundred, come back for
        the rest" work.
        """
        for existing in unfinished(folder, include_finished=True):
            if link and existing.link == link:
                return existing
            if title and existing.title == title:
                return existing

        return cls(title=title or "Imported playlist", link=link)


def _plainly(seconds: int) -> str:
    """A wait, in words someone can act on."""
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours} hours"
    return f"{minutes} minutes" if minutes else "under a minute"


def unfinished(folder: Optional[Path] = None, *, include_finished: bool = False) -> list[ImportJob]:
    """Every import record on disk that still has work left."""
    folder = Path(folder or imports_dir())
    if not folder.is_dir():
        return []

    jobs = []
    for path in sorted(folder.glob("*.json")):
        job = ImportJob.load(path)
        if job is None:
            continue
        if include_finished or not job.finished:
            jobs.append(job)

    return jobs
