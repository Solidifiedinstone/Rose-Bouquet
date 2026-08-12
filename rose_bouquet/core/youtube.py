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
from typing import Optional

from rose_bouquet.core.recommend import Candidate
from rose_bouquet.core.tastes import Channel

logger = logging.getLogger(__name__)

CHANNEL_ID = re.compile(r"(?:channel/|@)([A-Za-z0-9_\-]+)")

#: How many uploads to read per channel when building the feed. Enough to catch
#: up after a week away, few enough that twenty subscriptions is not a stall.
UPLOADS_PER_CHANNEL = 12


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


def _options(extra: Optional[dict] = None) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
        # No cookies, no login, no client identity beyond a normal request.
        "extractor_args": {"youtubetab": {"skip": ["authcheck"]}},
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

    def uploads(self, identifier: str, limit: int = UPLOADS_PER_CHANNEL) -> list[Video]:
        """The most recent uploads from a channel."""
        identifier = channel_id(identifier) or identifier
        base = (f"https://www.youtube.com/channel/{identifier}"
                if identifier.startswith("UC")
                else f"https://www.youtube.com/@{identifier}")

        data = self._extract(f"{base}/videos", {"playlistend": limit})
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

    def search(self, query: str, limit: int = 20) -> list[Video]:
        """Search YouTube itself — videos, not just music."""
        if not query.strip():
            return []

        data = self._extract(f"ytsearch{limit}:{query}")
        if not data:
            return []

        return [
            _video(entry) for entry in (data.get("entries") or [])
            if isinstance(entry, dict) and entry.get("id")
        ]

    def related(self, video_id: str, limit: int = 10) -> list[Video]:
        """What YouTube considers related to a video.

        Used as *candidates* for the local ranker rather than as recommendations
        in their own right: the ordering here is YouTube's opinion, and the
        whole point of this app is that the final ordering is yours.
        """
        data = self._extract(f"https://www.youtube.com/watch?v={video_id}",
                             {"extract_flat": False, "playlistend": limit})
        if not data:
            return []

        related = data.get("related_videos") or []
        return [
            _video(entry) for entry in related[:limit]
            if isinstance(entry, dict) and entry.get("id")
        ]

    # ── Streaming ─────────────────────────────────────────────────

    def stream_url(self, video_id: str, *, audio_only: bool = True) -> str:
        """A direct media URL, for playing without downloading.

        Audio-only by default: this is a music app, and streaming video to play
        a song wastes bandwidth on both ends.
        """
        options = {
            "format": "bestaudio/best" if audio_only else "best",
            "extract_flat": False,
        }
        data = self._extract(f"https://www.youtube.com/watch?v={video_id}", options)
        if not data:
            return ""

        url = data.get("url")
        if url:
            return url

        formats = data.get("formats") or []
        for candidate in reversed(formats):
            if not isinstance(candidate, dict):
                continue
            if audio_only and candidate.get("vcodec") not in (None, "none"):
                continue
            if candidate.get("url"):
                return candidate["url"]
        return ""


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
    candidates: list[Candidate] = []

    for index, channel in enumerate(channels):
        if channel.muted:
            continue
        if report is not None:
            report(f"Checking {channel.title} ({index + 1} of {len(channels)})")

        for video in youtube.uploads(channel.id or channel.title, limit=per_channel):
            candidate = video.to_candidate("subscription")
            candidate.channel_id = candidate.channel_id or channel.id
            candidates.append(candidate)

    return candidates
