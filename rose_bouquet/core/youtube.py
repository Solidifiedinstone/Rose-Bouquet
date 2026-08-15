"""YouTube proper — channels, uploads and videos — on top of the music layer.

`ytmusic.py` handles YouTube Music: songs, albums, artists, the music home feed.
This handles the rest of YouTube: subscribing to a channel, listing what it has
uploaded, and playing or downloading a video's audio.

Everything here works **without an account**. Channel uploads come from yt-dlp's
flat extraction, which reads the same public pages a browser would; nothing is
logged in, nothing is sent back, and no cookie jar is involved. The cost of that
is that the "subscriptions" are ours rather than YouTube's — see `tastes.py` —
and the benefit is that the profile they build stays on this machine.

Audio-only is the default even for videos, because this is a music app: a five
minute song should not stream a five minute video to play it. Video playback is
available for the times it is actually the point.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from rose_bouquet.core.media import Candidate, Channel

logger = logging.getLogger(__name__)

CHANNEL_ID = re.compile(r"(?:channel/|@)([A-Za-z0-9_\-]+)")

#: How many uploads to read per channel when building the feed. Enough to catch
#: up after a week away, few enough that twenty subscriptions is not a stall.
UPLOADS_PER_CHANNEL = 12

#: A "short" is a vertical video of a minute or less. YouTube does not label
#: them in a flat listing, so duration is what identifies one.
SHORT_SECONDS = 60

#: Shared with the downloader — see `ytmusic.PLAYER_CLIENTS`.
PLAYER_CLIENTS = ["tv", "android", "ios", "web"]

#: Clients used when the URL is handed to *something else* to fetch — which is
#: what streaming is: Qt opens the URL itself, with none of yt-dlp's session.
#:
#: This is a different problem from downloading, where yt-dlp does the fetching
#: and its own session makes any client's URL work. Several clients now hand
#: back URLs that answer 403 to anyone else — measured, not guessed: `tv`,
#: `web` and `mweb` all refuse, `android` serves. Asking for those first is why
#: a stream would play one minute and fail the next, depending on which client
#: happened to answer.
#: Asked in tiers rather than all at once. yt-dlp queries every client it is
#: given, in turn, whether or not the first one already worked — measured at
#: 4.4s for the full list against 0.96s for `android` alone. Since `android`
#: serves a playable URL nearly every time, asking it on its own first makes
#: pressing play four times faster, and the rest are still there for the times
#: it does not.
STREAM_CLIENTS = ["android"]
FALLBACK_STREAM_CLIENTS = ["web_safari", "ios", "tv", "web"]

#: How many channels to read at once. Bounded well below the number of
#: subscriptions: this is somebody's home connection and YouTube's patience,
#: not a datacentre.
CHANNEL_WORKERS = 8

#: How much of a stream to ask for when checking whether it is actually
#: playable. Small enough to be instant; the server either serves a range or
#: refuses the request outright, and either answer is the one being tested.
PROBE_BYTES = 2048

#: How many URLs to test before giving up on a tier. A video can offer thirty
#: formats, and testing all of them at six seconds each is minutes of waiting
#: to discover what the first two already said.
PROBE_LIMIT = 3

#: A probe is a round trip to a CDN that is either going to answer or refuse.
#: Six seconds was patience for a slow link; it is also six seconds of dead
#: air per format when something is wrong.
PROBE_TIMEOUT = 2.5


@dataclass
class Video:
    """One video, as much as a flat listing tells us about it."""

    id: str = ""
    title: str = ""
    channel: str = ""
    channel_id: str = ""
    duration: int = 0
    thumbnail: str = ""
    published: str = ""
    views: int = 0

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"

    @property
    def clock(self) -> str:
        minutes, seconds = divmod(max(0, self.duration), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @property
    def is_short(self) -> bool:
        return 0 < self.duration <= SHORT_SECONDS

    @property
    def thumbnail_url(self) -> str:
        """A picture for this video, falling back to YouTube's own url.

        Flat listings frequently omit thumbnails, and a wall of results with no
        pictures is just a list of filenames.
        """
        if self.thumbnail:
            return self.thumbnail
        return f"https://i.ytimg.com/vi/{self.id}/mqdefault.jpg" if self.id else ""

    def to_candidate(self, source: str = "subscription") -> Candidate:
        return Candidate(
            id=self.id, title=self.title, artist=self.channel,
            channel_id=self.channel_id, kind="video", published=self.published,
            duration=self.duration, thumbnail=self.thumbnail, source=source,
        )

    def to_channel(self) -> Channel:
        return Channel(id=self.channel_id, title=self.channel, kind="channel")


def channel_id(link: str) -> str:
    """The channel id or handle out of any YouTube channel link."""
    link = (link or "").strip()
    match = CHANNEL_ID.search(link)
    if match:
        return match.group(1)
    if link.startswith(("UC", "@")):
        return link.lstrip("@")
    return ""


def playable(url: str, *, timeout: float = PROBE_TIMEOUT) -> bool:
    """Whether something else can actually fetch this URL.

    Worth the round trip because the alternative is worse: a refused URL handed
    to the player produces "Could not open file" several seconds later, with no
    way to tell a dead link from a dead network. Asking for the first couple of
    kilobytes settles it in well under a second, and the answer is exactly the
    question — can a plain HTTP client read this.
    """
    if not url:
        return False
    if not url.startswith(("http://", "https://")):
        return True                      # a local file; nothing to check

    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"Range": f"bytes=0-{PROBE_BYTES - 1}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status in (200, 206)
    except urllib.error.HTTPError as exc:
        logger.debug("stream url answered %s", exc.code)
        return False
    except Exception as exc:             # noqa: BLE001 — a probe must never raise
        # A network problem is not the URL's fault, and refusing to play
        # because a probe timed out would be worse than trying anyway.
        logger.debug("could not probe the stream url: %s", exc)
        return True


def _options(extra: Optional[dict] = None) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
        # No cookies and no login — but the default web client now refuses
        # anonymous requests, so the TV and mobile clients are asked first.
        "extractor_args": {
            "youtubetab": {"skip": ["authcheck"]},
            "youtube": {"player_client": PLAYER_CLIENTS},
        },
    }
    options.update(extra or {})
    return options


def _published(entry: dict) -> str:
    """A best-effort upload date. Flat listings often give a relative one."""
    stamp = entry.get("upload_date")
    if isinstance(stamp, str) and len(stamp) == 8 and stamp.isdigit():
        return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"

    # "3 days ago", "2 weeks ago" — enough for freshness to mean something.
    text = str(entry.get("release_timestamp") or entry.get("_time_text") or "")
    match = re.match(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", text.lower())
    if match:
        count = int(match.group(1))
        days = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}
        when = datetime.now() - timedelta(days=count * days[match.group(2)])
        return when.date().isoformat()

    timestamp = entry.get("timestamp")
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        return datetime.fromtimestamp(timestamp).date().isoformat()

    return ""


def _video(entry: dict) -> Video:
    thumbnails = entry.get("thumbnails") or []
    thumbnail = ""
    if isinstance(thumbnails, list) and thumbnails:
        last = thumbnails[-1]
        if isinstance(last, dict):
            thumbnail = last.get("url", "")

    return Video(
        id=entry.get("id") or "",
        title=entry.get("title") or "",
        channel=entry.get("channel") or entry.get("uploader") or "",
        channel_id=entry.get("channel_id") or entry.get("uploader_id") or "",
        duration=int(entry.get("duration") or 0),
        thumbnail=thumbnail or entry.get("thumbnail") or "",
        published=_published(entry),
        views=int(entry.get("view_count") or 0),
    )


class YouTube:
    """Public YouTube, read without an account."""

    def __init__(self) -> None:
        self._failed = False

    @property
    def available(self) -> bool:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return False
        return True

    def _extract(self, url: str, options: Optional[dict] = None) -> Optional[dict]:
        try:
            import yt_dlp
        except ImportError:
            logger.info("yt-dlp is not installed; YouTube browsing is off")
            return None

        try:
            with yt_dlp.YoutubeDL(_options(options)) as extractor:
                return extractor.extract_info(url, download=False)
        except Exception as exc:                  # noqa: BLE001 — yt-dlp raises widely
            logger.warning("could not read %s: %s", url, exc)
            return None

    # ── Channels ──────────────────────────────────────────────────

    def channel(self, identifier: str) -> Optional[Channel]:
        """Look up a channel by id, handle or link, without subscribing to it."""
        identifier = channel_id(identifier) or identifier
        url = (f"https://www.youtube.com/channel/{identifier}"
               if identifier.startswith("UC")
               else f"https://www.youtube.com/@{identifier}")

        data = self._extract(url, {"playlistend": 1})
        if not data:
            return None

        return Channel(
            id=data.get("channel_id") or data.get("uploader_id") or identifier,
            title=data.get("channel") or data.get("uploader") or data.get("title") or identifier,
            thumbnail=(data.get("thumbnails") or [{}])[-1].get("url", "")
            if isinstance(data.get("thumbnails"), list) else "",
            kind="channel",
        )

    def uploads(self, identifier: str, limit: int = UPLOADS_PER_CHANNEL,
                *, tab: str = "videos") -> list[Video]:
        """The most recent uploads from a channel.

        `tab` picks which shelf: "videos", "shorts", or "streams". They are
        separate pages on YouTube, and a channel that posts mostly shorts looks
        empty if you only ever ask for videos.
        """
        identifier = channel_id(identifier) or identifier
        base = (f"https://www.youtube.com/channel/{identifier}"
                if identifier.startswith("UC")
                else f"https://www.youtube.com/@{identifier}")

        tab = tab if tab in ("videos", "shorts", "streams") else "videos"
        data = self._extract(f"{base}/{tab}", {"playlistend": limit})
        if not data:
            return []

        entries = data.get("entries") or []
        videos = []
        for entry in entries[:limit]:
            if not isinstance(entry, dict):
                continue
            video = _video(entry)
            # A flat listing sometimes omits the channel on each row.
            video.channel = video.channel or data.get("channel", "")
            video.channel_id = video.channel_id or data.get("channel_id", "")
            if video.id:
                videos.append(video)
        return videos

    def search(self, query: str, limit: int = 20, *, shorts: bool = False) -> list[Video]:
        """Search YouTube itself — videos, not just music.

        `shorts` filters to the vertical ones. YouTube has no plain search
        filter for that, so it is done on duration: a short is at most a minute,
        and anything without a duration is left in rather than guessed at.
        """
        if not query.strip():
            return []

        # `#shorts` in the query is how YouTube itself is asked for them.
        # Filtering an ordinary search by duration finds almost nothing —
        # a search for "skateboard" returns full-length videos, correctly
        # discards every one, and leaves an empty screen.
        terms = f"{query} #shorts" if shorts else query
        data = self._extract(f"ytsearch{limit * 2 if shorts else limit}:{terms}")
        if not data:
            return []

        videos = [
            _video(entry) for entry in (data.get("entries") or [])
            if isinstance(entry, dict) and entry.get("id")
        ]

        if shorts:
            # The docstring's promise, which the code was not keeping: a flat
            # search frequently omits durations, and dropping every row that
            # has none meant a search for shorts returning nothing at all.
            # Known-short first, then the unknowns, then nothing else.
            known = [v for v in videos if 0 < v.duration <= SHORT_SECONDS]
            unknown = [v for v in videos if v.duration == 0]
            videos = known + unknown

        return videos[:limit]

    def related(self, video_id: str, limit: int = 10, *, title: str = "") -> list[Video]:
        """What YouTube considers related to a video.

        Used as *candidates* for the local ranker rather than as recommendations
        in their own right: the ordering here is YouTube's opinion, and the
        whole point of this app is that the final ordering is yours.

        Two sources, because the obvious one is gone. The watch page used to
        carry a `related_videos` list and no longer does — it now comes back
        empty for every video, which silently reduced the feed to subscription
        uploads and nothing else. What still works:

        1. The **mix** — `list=RD<id>` is a radio playlist YouTube builds around
           a video, which is its recommender's own output and the closest
           remaining thing to the old list. Not every video has one; anything
           outside music usually does not.
        2. **Search** on the title, as a fallback, which works for anything at
           all but is a weaker notion of "related".
        """
        mix = self._extract(
            f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}",
            {"extract_flat": True, "playlistend": limit + 1},
        )
        entries = (mix or {}).get("entries") or []

        videos = [
            _video(entry) for entry in entries
            if isinstance(entry, dict) and entry.get("id") and entry.get("id") != video_id
        ]
        if videos:
            return videos[:limit]

        if title:
            # The seed itself will come back in the results; it is dropped
            # rather than recommended back to someone who just watched it.
            return [v for v in self.search(title, limit=limit + 1) if v.id != video_id][:limit]

        return []

    # ── Streaming ─────────────────────────────────────────────────

    def stream_url(self, video_id: str, *, audio_only: bool = True) -> str:
        """A direct media URL, for playing without downloading.

        Audio-only by default: this is a music app, and streaming video to play
        a song wastes bandwidth on both ends.

        For video the selector asks for a *progressive* stream — one file with
        both tracks in it. YouTube's highest qualities are served as separate
        video and audio files expecting the player to mux them, which
        QMediaPlayer cannot do: it would play a silent picture. A slightly lower
        resolution that actually has sound is the right trade for a music app.
        """
        options = {
            "format": (
                # `bestaudio` alone can still hand back a muxed file when the
                # client offers no audio-only format, which quietly streams the
                # video anyway. Asking for no video codec first is what makes
                # "audio only" actually mean it.
                "bestaudio[vcodec=none]/bestaudio/best" if audio_only
                else "best[acodec!=none][vcodec!=none]/b[ext=mp4][acodec!=none]/best"
            ),
            "extract_flat": False,
            "extractor_args": {
                "youtubetab": {"skip": ["authcheck"]},
                "youtube": {"player_client": STREAM_CLIENTS},
            },
        }
        for clients in (STREAM_CLIENTS, FALLBACK_STREAM_CLIENTS):
            options["extractor_args"] = {
                "youtubetab": {"skip": ["authcheck"]},
                "youtube": {"player_client": clients},
            }
            data = self._extract(f"https://www.youtube.com/watch?v={video_id}", options)
            if not data:
                continue

            for attempt, url in enumerate(
                    self._candidate_urls(data, audio_only=audio_only)):
                if attempt >= PROBE_LIMIT:
                    # Everything this client offered was refused. Another
                    # client is far more likely to help than a thirtieth
                    # format from the same one.
                    break
                if playable(url):
                    return url
                logger.debug("stream url refused, trying the next format")

            logger.info("no playable stream from %s, widening the search", clients)

        return ""

    def _candidate_urls(self, data: dict, *, audio_only: bool):
        """Every URL worth trying for this video, best first."""
        seen: set[str] = set()

        url = data.get("url")
        if url:
            seen.add(url)
            yield url

        for candidate in reversed(data.get("formats") or []):
            if not isinstance(candidate, dict):
                continue
            if audio_only and candidate.get("vcodec") not in (None, "none"):
                continue
            found = candidate.get("url")
            if found and found not in seen:
                seen.add(found)
                yield found


# ── The feed ──────────────────────────────────────────────────────

def subscription_candidates(
    youtube: YouTube,
    channels: list[Channel],
    *,
    per_channel: int = UPLOADS_PER_CHANNEL,
    report=None,
) -> list[Candidate]:
    """Recent uploads from everything you follow, as ranking candidates.

    Muted channels are skipped entirely rather than ranked down — muting is a
    request not to see something, and honouring it partially is worse than not
    offering it.
    """
    wanted = [c for c in channels if not c.muted]
    if not wanted:
        return []

    # Fetched in parallel. Each channel is one network round trip of about half
    # a second, and forty of them in a row is twenty seconds of a progress bar
    # — nearly all of it spent waiting rather than working. Each call builds
    # its own extractor, so there is nothing shared to protect.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    candidates: list[Candidate] = []
    done = 0

    def fetch(channel):
        return channel, youtube.uploads(channel.id or channel.title, limit=per_channel)

    with ThreadPoolExecutor(max_workers=CHANNEL_WORKERS) as pool:
        jobs = [pool.submit(fetch, channel) for channel in wanted]
        for job in as_completed(jobs):
            done += 1
            try:
                channel, videos = job.result()
            except Exception as exc:            # noqa: BLE001 — one bad channel
                logger.debug("could not read a channel: %s", exc)
                continue

            if report is not None:
                report(f"Checked {done} of {len(wanted)} channels")

            for video in videos:
                candidate = video.to_candidate("subscription")
                candidate.channel_id = candidate.channel_id or channel.id
                candidates.append(candidate)

    return candidates


class StreamCache:
    """Stream URLs, resolved ahead of time and kept until they expire.

    Resolving one takes about a second and a half of network, which is fine
    when it happens while you decide what to watch and unbearable when it
    happens after you have already scrolled. So the reel asks for what is
    coming *next* while the current one plays, and by the time it is scrolled
    to, the answer is already here.

    YouTube's URLs carry an expiry a few hours out. They are held for rather
    less than that: a stale one fails at the player, which is exactly the
    situation the cache exists to avoid.
    """

    #: How long a resolved URL is trusted for.
    TTL_SECONDS = 1800

    #: How many to keep. Expired entries are only noticed when something looks
    #: them up, so without a ceiling an evening of scrolling shorts leaves a
    #: few thousand dead URLs in memory that nobody will ever ask for again.
    LIMIT = 120

    def __init__(self, youtube: "YouTube", workers: int = 3) -> None:
        import threading
        from concurrent.futures import ThreadPoolExecutor

        self.youtube = youtube
        self._urls: dict[tuple[str, bool], tuple[float, str]] = {}
        # Reentrant: `prefetch` holds this and then asks `cached`, which takes
        # it again. With a plain Lock that is a deadlock — and since prefetch
        # runs on the interface thread, it freezes the whole window.
        self._lock = threading.RLock()
        # Threads that will not hold the app open: a prefetch in flight must
        # not delay quitting, and a URL nobody asked for is not worth waiting
        # on. Python joins pool threads at exit unless told otherwise.
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="rose-stream")
        self._closed = False
        self._pending: set[tuple[str, bool]] = set()

    def cached(self, video_id: str, *, audio_only: bool = True) -> str:
        """A URL already resolved and still fresh, or "" — never any network."""
        import time

        key = (video_id, audio_only)
        with self._lock:
            found = self._urls.get(key)
            if found is None:
                return ""
            at, url = found
            if time.time() - at > self.TTL_SECONDS:
                del self._urls[key]
                return ""
            return url

    def resolve(self, video_id: str, *, audio_only: bool = True) -> str:
        """The URL, fetching it if it is not already here. Blocks."""
        cached = self.cached(video_id, audio_only=audio_only)
        if cached:
            return cached

        url = self.youtube.stream_url(video_id, audio_only=audio_only)
        if url:
            self._store(video_id, audio_only, url)
        return url

    def prefetch(self, video_ids, *, audio_only: bool = True) -> None:
        """Start resolving these in the background. Returns at once."""
        if self._closed:
            return

        for video_id in video_ids:
            key = (video_id, audio_only)
            with self._lock:
                if key in self._pending or self.cached(video_id, audio_only=audio_only):
                    continue
                self._pending.add(key)
            self._pool.submit(self._fetch, video_id, audio_only)

    def _fetch(self, video_id: str, audio_only: bool) -> None:
        try:
            url = self.youtube.stream_url(video_id, audio_only=audio_only)
            if url:
                self._store(video_id, audio_only, url)
        except Exception as exc:            # noqa: BLE001 — a prefetch may fail quietly
            logger.debug("could not prefetch %s: %s", video_id, exc)
        finally:
            with self._lock:
                self._pending.discard((video_id, audio_only))

    def _store(self, video_id: str, audio_only: bool, url: str) -> None:
        import time

        with self._lock:
            self._urls[(video_id, audio_only)] = (time.time(), url)

            if len(self._urls) > self.LIMIT:
                # Oldest first: a URL resolved long ago is both nearest to
                # expiring and least likely to be wanted again.
                for key, _ in sorted(self._urls.items(), key=lambda kv: kv[1][0])[
                        :len(self._urls) - self.LIMIT]:
                    self._urls.pop(key, None)

    def close(self) -> None:
        """Stop prefetching. Safe to call twice."""
        if not self._closed:
            self._closed = True
            self._pool.shutdown(wait=False, cancel_futures=True)

    def forget(self, video_id: str) -> None:
        """Drop a video's URLs — it played badly, or is long gone from view."""
        with self._lock:
            for audio_only in (True, False):
                self._urls.pop((video_id, audio_only), None)


def discover(
    youtube: "YouTube",
    terms: Sequence[str],
    *,
    shorts: bool = False,
    per_term: int = 12,
    report=None,
) -> list[Candidate]:
    """Search several topics at once and pool the results.

    This is the half of a feed that is not a subscription box. Given the
    subjects somebody actually watches — see `interests.search_terms` — it goes
    and finds things by those subjects, from channels they have never heard of.

    Searched in parallel because each term is its own round trip and eight of
    them in a row is eight seconds of waiting.
    """
    terms = [term for term in terms if term and term.strip()]
    if not terms:
        return []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    found: list[Candidate] = []
    seen: set[str] = set()
    done = 0

    def look(term: str):
        return term, youtube.search(term, limit=per_term, shorts=shorts)

    with ThreadPoolExecutor(max_workers=min(CHANNEL_WORKERS, len(terms))) as pool:
        jobs = [pool.submit(look, term) for term in terms]
        for job in as_completed(jobs):
            done += 1
            try:
                _term, videos = job.result()
            except Exception as exc:        # noqa: BLE001 — one dead search
                logger.debug("a search failed: %s", exc)
                continue

            if report is not None:
                report(f"Looked at {done} of {len(terms)} topics")

            for video in videos:
                if video.id in seen:
                    continue
                seen.add(video.id)
                candidate = video.to_candidate("shorts" if shorts else "discover")
                found.append(candidate)

    return found


def similar_channels(youtube: "YouTube", channels: Sequence, limit: int = 12) -> list[Channel]:
    """Channels like the ones already followed.

    Read from each channel's own "channels" shelf — the featured and related
    creators a channel points at itself. It is YouTube's own answer to "who
    else is like this", published on the page, and it needs no account to see.
    """
    if not channels:
        return []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    found: dict[str, Channel] = {}

    def shelf(channel):
        identifier = channel_id(channel.id or channel.title) or channel.id
        base = (f"https://www.youtube.com/channel/{identifier}"
                if identifier.startswith("UC")
                else f"https://www.youtube.com/@{identifier}")
        return youtube._extract(f"{base}/channels", {"playlistend": 20})

    with ThreadPoolExecutor(max_workers=min(CHANNEL_WORKERS, len(channels))) as pool:
        jobs = [pool.submit(shelf, channel) for channel in channels]
        for job in as_completed(jobs):
            try:
                data = job.result()
            except Exception:               # noqa: BLE001 — a channel may hide it
                continue

            for entry in (data or {}).get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                found_id = entry.get("channel_id") or entry.get("id") or ""
                title = entry.get("channel") or entry.get("title") or ""
                if found_id.startswith("UC") and found_id not in found:
                    found[found_id] = Channel(id=found_id, title=title, kind="channel")

    return list(found.values())[:limit]
