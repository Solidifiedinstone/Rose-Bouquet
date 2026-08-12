"""Importing a Spotify playlist, and being honest about what did not come across.

Spotify does not let anything take the audio, so importing a playlist means
importing the *track list* and finding each song again on YouTube Music. Most of
them match. Some never will — a regional exclusive, a version that only exists
on Spotify, a title so generic the search finds the wrong thing.

So the point of this module is as much the misses as the hits. Every import
returns what matched **and** what did not, with the original artist and title
kept, so the list can be shown, retried, searched by hand, or exported. An
importer that silently drops a fifth of a playlist is worse than one that
refuses to run, because you find out months later when the song does not play.

Three ways in, in the order they are tried:

  1. **A public playlist link.** Uses the same embed endpoint the web player
     serves to `<iframe>`s, which needs no account and no API key.
  2. **API credentials**, if the user has set them — more reliable, handles
     long playlists properly, needs a free Spotify developer app.
  3. **A pasted or exported track list** — CSV from Exportify, or plain
     "Artist - Title" lines. Always works, needs nothing.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from io import StringIO
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PLAYLIST_ID = re.compile(r"(?:playlist[/:])([A-Za-z0-9]+)")
NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
EMBED_URL = "https://open.spotify.com/embed/playlist/{id}"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1/playlists/{id}/tracks"

TIMEOUT = 20


@dataclass
class SpotifyTrack:
    """One row of a Spotify playlist, before anything has been found for it."""

    title: str = ""
    artist: str = ""
    album: str = ""
    duration: int = 0

    @property
    def query(self) -> str:
        return f"{self.artist} {self.title}".strip()

    def __str__(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


@dataclass
class ImportReport:
    """What an import found, and what it did not."""

    title: str = ""
    matched: list[tuple[SpotifyTrack, object]] = field(default_factory=list)
    missed: list[SpotifyTrack] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.missed)

    @property
    def summary(self) -> str:
        if not self.total:
            return "Nothing to import"
        if not self.missed:
            return f"All {self.total} tracks found"
        return f"{len(self.matched)} of {self.total} found · {len(self.missed)} missing"

    def missed_lines(self) -> list[str]:
        return [str(track) for track in self.missed]


def playlist_id(link: str) -> str:
    """The id out of any form of Spotify playlist link, or "" if there is none."""
    link = (link or "").strip()
    match = PLAYLIST_ID.search(link)
    if match:
        return match.group(1)
    # A bare id pasted on its own.
    if re.fullmatch(r"[A-Za-z0-9]{20,26}", link):
        return link
    return ""


# ── Reading the playlist ──────────────────────────────────────────

def from_embed(link: str) -> tuple[str, list[SpotifyTrack]]:
    """Read a public playlist through the embed endpoint. No account needed.

    This is an undocumented endpoint and Spotify may change its shape at any
    time, so every step is defensive and a failure returns nothing rather than
    raising — the credential and paste routes are still there.
    """
    identifier = playlist_id(link)
    if not identifier:
        return "", []

    try:
        import requests

        response = requests.get(
            EMBED_URL.format(id=identifier),
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (rose-bouquet)"},
        )
        response.raise_for_status()
    except Exception as exc:                      # noqa: BLE001
        logger.warning("could not fetch the Spotify embed: %s", exc)
        return "", []

    match = NEXT_DATA.search(response.text)
    if not match:
        logger.warning("the Spotify embed did not contain the expected data")
        return "", []

    try:
        data = json.loads(match.group(1))
    except ValueError:
        return "", []

    entity = _dig(data, "props", "pageProps", "state", "data", "entity") or {}
    title = entity.get("name") or entity.get("title") or ""

    rows = entity.get("trackList") or entity.get("tracks") or []
    if isinstance(rows, dict):
        rows = rows.get("items", [])

    tracks = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        inner = row.get("track") if isinstance(row.get("track"), dict) else row
        tracks.append(SpotifyTrack(
            title=inner.get("title") or inner.get("name") or "",
            artist=_artist_names(inner),
            album=_album_name(inner),
            duration=int((inner.get("duration") or inner.get("duration_ms") or 0) // 1000)
            if isinstance(inner.get("duration") or inner.get("duration_ms"), (int, float)) else 0,
        ))

    return title, [t for t in tracks if t.title]


def from_api(link: str, client_id: str, client_secret: str) -> tuple[str, list[SpotifyTrack]]:
    """Read a playlist through the real API, using the user's own credentials.

    Client-credentials flow: no user login, no scopes, works for public
    playlists, and — unlike the embed — pages properly through long ones.
    """
    identifier = playlist_id(link)
    if not identifier or not client_id or not client_secret:
        return "", []

    try:
        import requests

        token_response = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=TIMEOUT,
        )
        token_response.raise_for_status()
        token = token_response.json().get("access_token", "")
        if not token:
            return "", []

        headers = {"Authorization": f"Bearer {token}"}
        tracks: list[SpotifyTrack] = []
        url = API_URL.format(id=identifier)
        params = {"limit": 100, "fields": "items(track(name,artists(name),album(name),duration_ms)),next"}

        while url:
            page = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
            page.raise_for_status()
            payload = page.json()

            for item in payload.get("items", []):
                inner = (item or {}).get("track") or {}
                if not inner.get("name"):
                    continue
                tracks.append(SpotifyTrack(
                    title=inner.get("name", ""),
                    artist=_artist_names(inner),
                    album=_album_name(inner),
                    duration=int(inner.get("duration_ms", 0) // 1000),
                ))

            url = payload.get("next")
            params = None            # `next` already carries the paging

        return "", tracks
    except Exception as exc:                      # noqa: BLE001
        logger.warning("the Spotify API call failed: %s", exc)
        return "", []


def from_text(text: str) -> list[SpotifyTrack]:
    """A pasted list, or an Exportify CSV. Always available, never fails.

    Handles "Artist - Title", "Title - Artist" is not guessed at (it would get
    it wrong half the time), and a CSV is detected by its header rather than by
    the file extension, so a `.txt` saved out of a spreadsheet still works.
    """
    text = (text or "").strip()
    if not text:
        return []

    first = text.splitlines()[0].lower()
    if "," in first and ("track name" in first or "artist name" in first or "title" in first):
        return _from_csv(text)

    tracks = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if " - " in line:
            artist, _, title = line.partition(" - ")
            tracks.append(SpotifyTrack(title=title.strip(), artist=artist.strip()))
        else:
            tracks.append(SpotifyTrack(title=line))
    return tracks


def _from_csv(text: str) -> list[SpotifyTrack]:
    tracks = []
    reader = csv.DictReader(StringIO(text))

    for row in reader:
        lowered = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        title = lowered.get("track name") or lowered.get("title") or lowered.get("name", "")
        artist = lowered.get("artist name(s)") or lowered.get("artist name") or lowered.get("artist", "")
        album = lowered.get("album name") or lowered.get("album", "")
        if title:
            tracks.append(SpotifyTrack(title=title, artist=artist, album=album))
    return tracks


# ── Matching ──────────────────────────────────────────────────────

def match_all(
    tracks: list[SpotifyTrack],
    finder: Callable[[str, str], Optional[object]],
    *,
    progress: Optional[Callable[[int, int, SpotifyTrack], None]] = None,
) -> ImportReport:
    """Find each track with `finder`, keeping the ones that came up empty.

    `finder` takes (title, artist) and returns something or None — normally
    `YouTubeMusic.best_match`, but anything with that shape does, which is what
    makes this testable without a network.
    """
    report = ImportReport()

    for index, track in enumerate(tracks):
        if progress is not None:
            progress(index, len(tracks), track)

        try:
            found = finder(track.title, track.artist)
        except Exception as exc:                  # noqa: BLE001
            logger.debug("match failed for %s: %s", track, exc)
            found = None

        if found is None:
            report.missed.append(track)
        else:
            report.matched.append((track, found))

    return report


def _artist_names(entry: dict) -> str:
    artists = entry.get("artists")
    if isinstance(artists, list):
        names = []
        for artist in artists:
            if isinstance(artist, dict):
                names.append(artist.get("name", ""))
            elif isinstance(artist, str):
                names.append(artist)
        return ", ".join(n for n in names if n)
    if isinstance(artists, str):
        return artists
    subtitle = entry.get("subtitle")
    return subtitle if isinstance(subtitle, str) else ""


def _album_name(entry: dict) -> str:
    album = entry.get("album")
    if isinstance(album, dict):
        return album.get("name", "")
    return album if isinstance(album, str) else ""


def _dig(data: dict, *keys: str):
    """Walk a nested dict, giving up quietly rather than raising."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
