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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import StringIO
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PLAYLIST_ID = re.compile(r"(?:playlist[/:])([A-Za-z0-9]+)")
NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
EMBED_URL = "https://open.spotify.com/embed/playlist/{id}"
#: The token the web player itself uses. No account, no registration — it is
#: what makes paging a long playlist possible without credentials.
ANON_TOKEN_URL = ("https://open.spotify.com/get_access_token"
                  "?reason=transport&productType=web_player")
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1/playlists/{id}/tracks"

TIMEOUT = 20

#: How many lookups run at once. Enough to overlap the waiting, few enough that
#: YouTube Music does not start refusing.
MATCH_WORKERS = 6

#: Report progress every this many tracks rather than on every one.
PROGRESS_EVERY = 5


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
    #: Matched on YouTube, but the audio never arrived: (track, why).
    #: Filled in after the report is first shown, as downloads come back.
    failed: list[tuple[SpotifyTrack, str]] = field(default_factory=list)
    #: Set when the source probably had more tracks than we could read.
    truncated: bool = False

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.missed)

    @property
    def summary(self) -> str:
        if not self.total:
            return "Nothing to import"
        parts = []
        if self.missed:
            parts.append(f"{len(self.missed)} missing")
        if self.failed:
            parts.append(f"{len(self.failed)} failed to download")
        if not parts:
            return f"All {self.total} tracks found"
        return f"{len(self.matched)} of {self.total} found · " + " · ".join(parts)

    def missed_lines(self) -> list[str]:
        """Everything that did not end up as audio, in one list.

        A track nobody could find and a track that was found but would not
        download are different problems with the same consequence — it is not
        in your library. Splitting them across two screens meant the download
        failures were only ever a toast that scrolled past, so they were the
        ones people lost. The reason travels with the line.
        """
        lines = [str(track) for track in self.missed]
        lines += [f"{track} — download failed: {why}" if why else f"{track} — download failed"
                  for track, why in self.failed]
        return lines

    def note_download_failure(self, track: SpotifyTrack, why: str = "") -> None:
        """Record a download that did not land. Idempotent per track.

        Retries call this again for the same track, and a row that appears
        twice in the list reads as two lost songs.
        """
        key = str(track)
        for index, (existing, _why) in enumerate(self.failed):
            if str(existing) == key:
                self.failed[index] = (existing, why.strip())
                return
        self.failed.append((track, why.strip()))

    def note_download_recovered(self, track: SpotifyTrack) -> None:
        """Take a track back off the failed list — a retry worked."""
        key = str(track)
        self.failed = [pair for pair in self.failed if str(pair[0]) != key]


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


def spotdl_credentials() -> tuple[str, str]:
    """Spotify app credentials from spotdl, if it is installed.

    spotdl registers its own application and ships the credentials, which is
    how it reads playlists of any length. Using them here — when the user has
    installed spotdl — is the same arrangement, and it means a long playlist
    works with no setup. The user's own credentials always win when set.
    """
    try:
        from spotdl.utils.config import DEFAULT_CONFIG

        return DEFAULT_CONFIG.get("client_id", ""), DEFAULT_CONFIG.get("client_secret", "")
    except Exception:                             # noqa: BLE001 — not installed
        return "", ""


def client_token(client_id: str, client_secret: str) -> str:
    """A client-credentials token. Empty when it cannot be had."""
    if not client_id or not client_secret:
        return ""
    try:
        import requests

        response = requests.post(
            TOKEN_URL, data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret), timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("access_token", "")
    except Exception as exc:                      # noqa: BLE001
        logger.warning("could not get a Spotify token: %s", exc)
        return ""


@dataclass
class Page:
    """One page of a playlist, and what to do next."""

    tracks: list["SpotifyTrack"] = field(default_factory=list)
    #: Where to carry on from. None when the playlist is finished.
    next_offset: Optional[int] = None
    #: How many tracks the playlist says it has, when it says.
    total: int = 0
    #: Seconds to wait before trying again, when Spotify asked us to stop.
    retry_after: int = 0
    error: str = ""


def fetch_page(playlist_id: str, token: str, offset: int = 0, limit: int = 100) -> Page:
    """One page of a playlist, from wherever you left off.

    Rate limiting is reported rather than raised, because being told "come back
    in an hour" halfway through a nine-hundred-track playlist is a normal thing
    that has to be survivable: the caller writes down the offset and carries on
    later.
    """
    try:
        import requests

        response = requests.get(
            API_URL.format(id=playlist_id),
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": limit, "offset": offset,
                    "fields": "items(track(name,artists(name),album(name),duration_ms)),total"},
            timeout=TIMEOUT,
        )
    except Exception as exc:                      # noqa: BLE001
        return Page(next_offset=offset, error=str(exc))

    if response.status_code == 429:
        try:
            wait = int(response.headers.get("Retry-After", "60"))
        except ValueError:
            wait = 60
        return Page(next_offset=offset, retry_after=wait,
                    error="Spotify is rate-limiting this connection")

    if not response.ok:
        return Page(next_offset=offset, error=f"Spotify said {response.status_code}")

    try:
        payload = response.json()
    except ValueError:
        return Page(next_offset=offset, error="Spotify sent something unreadable")

    items = payload.get("items") or []
    tracks = []
    for item in items:
        inner = (item or {}).get("track") or {}
        if not inner.get("name"):
            continue
        tracks.append(SpotifyTrack(
            title=inner.get("name", ""),
            artist=_artist_names(inner),
            album=_album_name(inner),
            duration=int(inner.get("duration_ms", 0) // 1000),
        ))

    total = int(payload.get("total") or 0)
    read_so_far = offset + len(items)
    finished = not items or (total and read_so_far >= total)

    return Page(tracks=tracks, total=total,
                next_offset=None if finished else read_so_far)


def anonymous_token() -> str:
    """An access token from the public web player. Empty if it cannot be had."""
    try:
        import requests

        response = requests.get(
            ANON_TOKEN_URL, timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (rose-bouquet)"},
        )
        response.raise_for_status()
        token = response.json().get("accessToken", "")
    except Exception as exc:                      # noqa: BLE001
        logger.info("no anonymous Spotify token: %s", exc)
        return ""

    return token if isinstance(token, str) else ""


def _page_tracks(get_json, identifier: str, *, per_page: int = 100,
                 ceiling: int = 10000) -> list[SpotifyTrack]:
    """Walk every page of a playlist. `get_json(url, params)` does the fetching.

    Injected rather than hard-coded so the paging itself — the part that was
    broken — is testable without a network or an account.

    The ceiling is a runaway guard, not a limit anybody should hit: it is twice
    Spotify's own maximum playlist size.
    """
    tracks: list[SpotifyTrack] = []
    offset = 0

    while offset < ceiling:
        payload = get_json(
            API_URL.format(id=identifier),
            {"limit": per_page, "offset": offset,
             "fields": "items(track(name,artists(name),album(name),duration_ms)),total"},
        )
        if not isinstance(payload, dict):
            break

        items = payload.get("items")
        if not isinstance(items, list) or not items:
            break

        for item in items:
            inner = (item or {}).get("track") or {}
            if not inner.get("name"):
                # Local files and removed tracks come back as empty entries.
                # They are genuinely missing, and skipping them here means the
                # import report will not claim to have found them.
                continue
            tracks.append(SpotifyTrack(
                title=inner.get("name", ""),
                artist=_artist_names(inner),
                album=_album_name(inner),
                duration=int(inner.get("duration_ms", 0) // 1000),
            ))

        offset += len(items)
        total = payload.get("total")
        if isinstance(total, int) and offset >= total:
            break

    return tracks


def from_public_api(link: str, token: str = "") -> tuple[str, list[SpotifyTrack]]:
    """Read a playlist in full, with no credentials, by paging the web API.

    This is the route that fixes long playlists: the embed endpoint returns only
    the first hundred tracks and gives no hint that it truncated, so a 400-track
    playlist silently imported as 100.
    """
    identifier = playlist_id(link)
    if not identifier:
        return "", []

    token = token or anonymous_token()
    if not token:
        return "", []

    try:
        import requests

        headers = {"Authorization": f"Bearer {token}"}

        def get_json(url: str, params: dict):
            response = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()

        tracks = _page_tracks(get_json, identifier)

        title = ""
        try:
            details = get_json(
                f"https://api.spotify.com/v1/playlists/{identifier}", {"fields": "name"}
            )
            title = details.get("name", "") if isinstance(details, dict) else ""
        except Exception:                         # noqa: BLE001 — the name is a nicety
            pass

        return title, tracks
    except Exception as exc:                      # noqa: BLE001
        logger.warning("the public Spotify API failed: %s", exc)
        return "", []


def read_all(
    link: str,
    *,
    client_id: str = "",
    client_secret: str = "",
    start_offset: int = 0,
    report=None,
) -> Page:
    """Read a playlist from `start_offset` to the end, however many pages that takes.

    This is the route that makes a long playlist work. It pages until Spotify
    says there is no more, and if it is cut short — rate limiting, a dropped
    connection — it returns what it got *and where to carry on from*, so the
    import can be finished later rather than started again.
    """
    identifier = playlist_id(link)
    if not identifier:
        return Page(error="That is not a Spotify playlist link")

    token = client_token(client_id, client_secret)
    if not token:
        borrowed_id, borrowed_secret = spotdl_credentials()
        token = client_token(borrowed_id, borrowed_secret)
    if not token:
        return Page(next_offset=start_offset,
                    error="No Spotify credentials available — add them in "
                          "Settings, or install spotdl")

    gathered: list[SpotifyTrack] = []
    offset = start_offset
    total = 0

    while True:
        page = fetch_page(identifier, token, offset)
        gathered.extend(page.tracks)
        total = page.total or total

        if page.error:
            return Page(tracks=gathered, next_offset=page.next_offset, total=total,
                        retry_after=page.retry_after, error=page.error)

        if report is not None and total:
            report(f"Read {start_offset + len(gathered)} of {total} tracks")

        if page.next_offset is None:
            return Page(tracks=gathered, next_offset=None, total=total)

        offset = page.next_offset


def fetch_playlist(
    link: str,
    client_id: str = "",
    client_secret: str = "",
) -> tuple[str, list[SpotifyTrack]]:
    """Read a playlist by whatever route works, most complete first.

    Order matters. The embed endpoint is the most reliable but caps at 100
    tracks, so it is the *fallback*, not the first choice — and if it is all we
    have, and it returned exactly its limit, the caller is told the count may be
    short rather than left to assume the playlist was small.
    """
    if not playlist_id(link):
        return "", []

    title, tracks = from_public_api(link)
    if tracks:
        return title, tracks

    if client_id and client_secret:
        title, tracks = from_api(link, client_id, client_secret)
        if tracks:
            return title, tracks

    return from_embed(link)


EMBED_LIMIT = 100


def looks_truncated(tracks: list[SpotifyTrack]) -> bool:
    """Whether a track list is suspiciously exactly one embed page long."""
    return len(tracks) == EMBED_LIMIT


# ── Matching ──────────────────────────────────────────────────────

def match_all(
    tracks: list[SpotifyTrack],
    finder: Callable[[str, str], Optional[object]],
    *,
    progress: Optional[Callable[[int, int, SpotifyTrack], None]] = None,
    workers: int = MATCH_WORKERS,
) -> ImportReport:
    """Find each track with `finder`, keeping the ones that came up empty.

    `finder` takes (title, artist) and returns something or None — normally
    `YouTubeMusic.best_match`, but anything with that shape does, which is what
    makes this testable without a network.
    """
    report = ImportReport()
    tracks = list(tracks)
    if not tracks:
        return report

    def look_up(index_and_track):
        index, track = index_and_track
        try:
            return index, track, finder(track.title, track.artist)
        except Exception as exc:                  # noqa: BLE001
            logger.debug("match failed for %s: %s", track, exc)
            return index, track, None

    # Each lookup is a network round trip that spends its time waiting, so they
    # overlap. A 400-track playlist took a round trip per track in a row —
    # long enough that the import looked like it had hung.
    results: list[tuple[int, SpotifyTrack, Optional[object]]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for done, outcome in enumerate(pool.map(look_up, enumerate(tracks))):
            results.append(outcome)
            # Reported every few tracks rather than every one: the interface
            # cannot show 400 updates and does not need to.
            if progress is not None and (done % PROGRESS_EVERY == 0 or done == len(tracks) - 1):
                progress(done + 1, len(tracks), outcome[1])

    # Sorted back into playlist order — the pool returns them as they finish.
    results.sort(key=lambda row: row[0])
    for _index, track, found in results:
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
