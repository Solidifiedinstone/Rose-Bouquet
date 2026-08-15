"""What an album actually contains, so the gaps in yours can be seen.

The library only ever knew about files on disk, so an album you had four
tracks of *was* a four-track album as far as the app was concerned. There was
no way to tell "this is an EP" from "this is half a record", which is exactly
the thing you want to know when you are looking at it.

MusicBrainz answers that: it is a public catalogue with no account, no API key
and no tracking, which is the same bargain the rest of this app makes. What it
gives back is the release's real tracklist — positions, titles, lengths — and
the local files are matched into it. Anything left over is a track you do not
have, and can be fetched from YouTube Music like any other missing song.

Two things it demands, both honoured here:

* **A real User-Agent with contact details.** Anonymous scrapers get blocked,
  and rightly.
* **One request per second, at most.** So results are cached on disk and
  requests are spaced. A cached tracklist is not re-fetched — album tracklists
  do not change, and a wrong cache entry is fixed by deleting the file.

Nothing here raises. A lookup that fails returns None, and the album view
falls back to showing what is on disk, which is what it always did.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rose_bouquet.core.library import data_dir

logger = logging.getLogger(__name__)

SEARCH_URL = "https://musicbrainz.org/ws/2/release"
TIMEOUT = 12

#: MusicBrainz asks for one request a second and means it. Shared across
#: threads, because the album view can open several lookups at once.
_MIN_INTERVAL = 1.1
_last_request = 0.0
_lock = threading.Lock()

#: Sent on every request. MusicBrainz requires an application, a version and a
#: way to get in touch; a generic browser string is what gets a client banned.
USER_AGENT = (
    "RoseBouquet/0.2 ( https://github.com/Solidifiedinstone/Rose-Bouquet )"
)


@dataclass
class CatalogueTrack:
    """One track as the catalogue has it, not as your disk has it."""

    position: int = 0
    title: str = ""
    #: Seconds. Zero when the catalogue does not say.
    duration: int = 0

    @property
    def clock(self) -> str:
        if not self.duration:
            return ""
        return f"{self.duration // 60}:{self.duration % 60:02d}"


@dataclass
class Release:
    """A release and its tracks."""

    title: str = ""
    artist: str = ""
    mbid: str = ""
    date: str = ""
    tracks: list[CatalogueTrack] = field(default_factory=list)

    @property
    def year(self) -> str:
        return self.date[:4] if self.date[:4].isdigit() else ""


def cache_dir() -> Path:
    folder = data_dir() / "tracklists"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def normalise(text: str) -> str:
    """A comparable form of a title.

    Files are tagged by hand, by rippers and by three different downloaders,
    so "Track 1", "01 - Track 1" and "Track  1 (Remastered)" all mean the same
    song. This strips the noise that differs between those without stripping
    the words that identify the song.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold()
    # A leading track number, as rippers write it.
    text = re.sub(r"^\s*\d{1,2}\s*[-._)]\s*", "", text)
    # Bracketed asides: remaster notes, years, "explicit", featured artists.
    text = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", text)
    text = re.sub(r"\b(remaster(ed)?|explicit|clean|bonus track|deluxe)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _cache_path(artist: str, album: str) -> Path:
    import hashlib

    key = hashlib.sha1(f"{normalise(artist)}|{normalise(album)}".encode()).hexdigest()
    return cache_dir() / f"{key}.json"


def _wait_turn() -> None:
    """Space requests a second apart, whoever is asking."""
    global _last_request
    with _lock:
        gap = time.monotonic() - _last_request
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap)
        _last_request = time.monotonic()


def _get(url: str, params: dict):
    """One request, with the rate limit honoured and 503 waited out.

    MusicBrainz answers 503 when you have asked too often — not because
    anything is broken. Spacing requests inside this process is not enough on
    its own: the limit is per address, so a second copy of the app, or this
    one restarted, starts again with an empty clock. A short backoff turns
    that from a failed lookup into a slow one.
    """
    import requests

    delay = 1.5
    for attempt in range(3):
        _wait_turn()
        response = requests.get(
            url, params=params,
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
        )
        if response.status_code != 503:
            response.raise_for_status()
            return response
        if attempt < 2:
            logger.debug("MusicBrainz asked us to slow down; waiting %.1fs", delay)
            time.sleep(delay)
            delay *= 2

    response.raise_for_status()
    return response


def _from_cache(path: Path) -> Optional[Release]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # A cached miss is remembered too, so a bootleg nobody catalogued is not
    # looked up again every time its album is opened.
    if not data.get("found"):
        return Release()
    return Release(
        title=data.get("title", ""),
        artist=data.get("artist", ""),
        mbid=data.get("mbid", ""),
        date=data.get("date", ""),
        tracks=[CatalogueTrack(position=t.get("position", 0),
                               title=t.get("title", ""),
                               duration=t.get("duration", 0))
                for t in data.get("tracks", []) if isinstance(t, dict)],
    )


def _to_cache(path: Path, release: Optional[Release]) -> None:
    payload = {"found": bool(release and release.tracks)}
    if release and release.tracks:
        payload.update({
            "title": release.title, "artist": release.artist,
            "mbid": release.mbid, "date": release.date,
            "tracks": [{"position": t.position, "title": t.title,
                        "duration": t.duration} for t in release.tracks],
        })
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as problem:
        logger.debug("could not cache a tracklist: %s", problem)


def tracklist(artist: str, album: str, *, refresh: bool = False) -> Optional[Release]:
    """The catalogue's tracklist for an album, or None if it is not known.

    Cached on disk, including the misses. Never raises: no network, a timeout
    or a shape MusicBrainz has since changed all read as "not known", and the
    album view goes on showing what is on disk.
    """
    if not album.strip():
        return None

    path = _cache_path(artist, album)
    if not refresh and path.exists():
        cached = _from_cache(path)
        if cached is not None:
            return cached if cached.tracks else None

    release = _lookup(artist, album)
    _to_cache(path, release)
    return release if release and release.tracks else None


def _lookup(artist: str, album: str) -> Optional[Release]:
    import importlib.util

    if importlib.util.find_spec("requests") is None:
        # The offline install is a supported way to run this.
        logger.info("requests is not installed, so tracklists are unavailable")
        return None

    query = f'release:"{_escape(album)}"'
    if artist.strip():
        query += f' AND artist:"{_escape(artist)}"'

    try:
        response = _get(SEARCH_URL, {"query": query, "fmt": "json", "limit": 5})
        releases = response.json().get("releases") or []
    except Exception as exc:                          # noqa: BLE001
        logger.info("could not search MusicBrainz for %r: %s", album, exc)
        return None

    best = _best_match(releases, artist, album)
    if best is None:
        return None

    return _fetch_tracks(best)


def _escape(text: str) -> str:
    """Lucene's specials, which album titles are full of."""
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", text or "")


def _best_match(releases: list, artist: str, album: str) -> Optional[dict]:
    """Pick a release from the candidates.

    An album is reissued, remastered, released in three countries and put in a
    box set, so a search returns many rows for one record. Preferring the
    earliest official release with a real tracklist gets the album as it was
    rather than a deluxe edition with six bonus tracks you were never missing.
    """
    wanted_album, wanted_artist = normalise(album), normalise(artist)
    scored = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        if normalise(release.get("title", "")) != wanted_album:
            continue
        credit = release.get("artist-credit") or []
        names = " ".join(
            part.get("name", "") for part in credit if isinstance(part, dict))
        if wanted_artist and normalise(names) != wanted_artist:
            continue
        official = (release.get("status") or "").lower() == "official"
        date = release.get("date") or "9999"
        scored.append((not official, date, release))

    if not scored:
        return None
    scored.sort(key=lambda row: (row[0], row[1]))
    return scored[0][2]


def _fetch_tracks(release: dict) -> Optional[Release]:
    try:
        response = _get(
            f"{SEARCH_URL}/{release.get('id', '')}",
            # artist-credits as well as recordings: without it the release
            # comes back with a tracklist and no artist on it.
            {"fmt": "json", "inc": "recordings+artist-credits"},
        )
        data = response.json()
    except Exception as exc:                          # noqa: BLE001
        logger.info("could not read a MusicBrainz release: %s", exc)
        return None

    tracks = []
    for medium in data.get("media") or []:
        if not isinstance(medium, dict):
            continue
        for track in medium.get("tracks") or []:
            if not isinstance(track, dict):
                continue
            length = track.get("length") or 0
            tracks.append(CatalogueTrack(
                position=len(tracks) + 1,
                title=track.get("title", ""),
                duration=int(length // 1000) if isinstance(length, (int, float)) else 0,
            ))

    if not tracks:
        return None

    credit = data.get("artist-credit") or []
    return Release(
        title=data.get("title", ""),
        artist=" ".join(part.get("name", "") for part in credit
                        if isinstance(part, dict)).strip(),
        mbid=data.get("id", ""),
        date=data.get("date", ""),
        tracks=tracks,
    )


@dataclass
class Slot:
    """One line of an album: the catalogue's track, and your file if you have it."""

    position: int = 0
    title: str = ""
    duration: int = 0
    #: The local Track, or None when this is a gap in your collection.
    track: object = None

    @property
    def owned(self) -> bool:
        return self.track is not None


def reconcile(release: Optional[Release], owned: list) -> list[Slot]:
    """Lay local files into the catalogue's tracklist.

    Matched on the normalised title rather than on position, because a file
    tagged with the wrong track number is common and a file tagged with the
    wrong *title* is not. Anything on disk the catalogue does not list — a
    bonus track, a mistagged file — is kept and appended rather than hidden:
    losing a track you own would be a far worse fault than showing an extra
    line.
    """
    if release is None or not release.tracks:
        return [Slot(position=index, title=t.display_title,
                     duration=getattr(t, "duration", 0), track=t)
                for index, t in enumerate(owned, start=1)]

    remaining = {}
    for track in owned:
        remaining.setdefault(normalise(track.display_title), []).append(track)

    slots = []
    for entry in release.tracks:
        key = normalise(entry.title)
        mine = remaining.get(key)
        slots.append(Slot(
            position=entry.position, title=entry.title,
            duration=entry.duration or 0,
            track=mine.pop(0) if mine else None,
        ))

    leftover = [t for tracks in remaining.values() for t in tracks]
    for extra in leftover:
        slots.append(Slot(position=len(slots) + 1, title=extra.display_title,
                          duration=getattr(extra, "duration", 0), track=extra))

    return slots
