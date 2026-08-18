"""YouTube Music: browsing, searching, and downloading.

Two libraries do the work, and the split matters. `ytmusicapi` talks to the same
private API the YouTube Music web app uses, which is what makes browsing feel
like the real thing — home feed, charts, related artists, real playlists. It
cannot download. `yt-dlp` downloads, and knows nothing about browsing. Rose
Bouquet uses each for the half it is good at.

Both are optional. Without them the app is a local music player that says so,
rather than an app that fails to start.

Downloads land as tagged files in the library folder, so anything pulled from
YouTube Music becomes an ordinary track: it plays offline, appears in playlists,
gets served to other devices, and survives this app being uninstalled.

**On the legal side:** downloading from YouTube is against YouTube's terms of
service, whatever the local copyright position on personal-use copies. This is
the same trade-off `yt-dlp` itself carries, it is the user's call to make, and
nothing here circumvents DRM or paywalled content.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from rose_bouquet.core.library import Track, data_dir

logger = logging.getLogger(__name__)

#: The fastest a download will report progress, in seconds.
PROGRESS_INTERVAL = 0.4

#: Which YouTube player clients to try, in order.
#:
#: YouTube does not offer every client the same formats, and the ones this app
#: used to ask — `tv`, `android`, `ios`, `web` — are offered no audio-only
#: stream at all when signed out. So `bestaudio` matched nothing, fell through
#: to itag 18 (a muxed 360p MP4 carrying about 96 kbps of AAC), and re-encoded
#: that to MP3. Eleven megabytes of video fetched and thrown away, to end up
#: with the worst audio on the platform.
#:
#: Other clients are given itag 251 (129 kbps Opus) and 140 (129 kbps AAC) with
#: no account at all. Same track: 3.3 MB instead of 11.3 MB, from a better
#: source. That is the whole trick — and it *is* the free tier. Itag 141, the
#: 256 kbps one, is what Premium actually buys, and it is absent either way.
#:
#: Offering a format and then serving it are separate questions, though, and
#: the second is why the order below is what it is. Downloading four tracks
#: with each client on its own, 2026-08-17:
#:
#: | client                        | got the audio |
#: |-------------------------------|---------------|
#: | `web_embedded`                | 4/4           |
#: | `tv_embedded`, `android_vr`   | 3/4           |
#: | `ios_music`, `android_music`  | 1/4           |
#: | `android`                     | 4/4, muxed    |
#: | `tv`                          | 0/4           |
#:
#: The failures are 403s on a URL that was handed over willingly, so the answer
#: is to ask the next client rather than to give up — which is what a list is
#: for. `android` stays at the end because a muxed stream is still the song,
#: and the tail of this list should be worse rather than nothing. `tv` is gone:
#: it no longer serves downloads at all.
#:
#: Streaming asks a *different* list, for a reason worth knowing before editing
#: this one — see `youtube.STREAM_CLIENTS`.
PLAYER_CLIENTS = [
    "web_embedded", "tv_embedded", "android_vr", "ios_music", "android_music",
    "android", "web",
]

#: What to ask yt-dlp for, in order of preference.
#:
#: `bestaudio` is not a formality: with the clients above it resolves to a real
#: audio-only stream, and the `/best` tail only matters when a video offers
#: nothing but muxed formats. Keeping the fallback is deliberate — it is what
#: Pear Desktop does for *every* download, because it asks clients that never
#: offer anything else, and a muxed stream with the picture thrown away is
#: still the song.
FORMAT = "bestaudio/best"


#: Refusals that are not really about the video: an age wall, a bot check, a
#: recording only a signed-in account may fetch. YouTube phrases them
#: differently and means the same thing every time — prove who you are — and
#: the answer is always the same too: the cookies from the browser you are
#: already signed in with.
SIGN_IN_WALLED = (
    "sign in to confirm your age",
    "sign in to confirm you",
    "inappropriate for some users",
    "age-restricted",
    "age restricted",
    "sign in to view",
    "login required",
    "private video",
    "is private",
    "members-only",
    "join this channel",
    "account associated with this video",
)


def is_a_sign_in_wall(message: str) -> bool:
    """Whether a failure is YouTube asking who you are rather than saying no."""
    lowered = message.lower().replace("\u2019", "'")
    return any(phrase in lowered for phrase in SIGN_IN_WALLED)


def browser_cookies() -> Optional[tuple]:
    """A yt-dlp `cookiesfrombrowser` tuple for a browser profile on this machine.

    Only used when it has been turned on in settings. Reading a cookie jar is
    not something an app should do behind your back, even locally.
    """
    from configparser import ConfigParser

    waterfox = Path.home() / ".waterfox"
    firefox = Path.home() / ".mozilla" / "firefox"

    for root in (waterfox, firefox):
        profiles = root / "profiles.ini"
        if not profiles.exists():
            continue

        parser = ConfigParser()
        try:
            parser.read(profiles)
        except Exception:                         # noqa: BLE001 — a broken ini is not fatal
            continue

        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            path = parser[section].get("Path", "")
            if not path:
                continue
            folder = (root / path) if parser[section].get("IsRelative", "1") == "1" else Path(path)
            if (folder / "cookies.sqlite").exists():
                # yt-dlp reads Waterfox with the Firefox reader, given the path.
                return ("firefox", str(folder), None, None)

    return None


#: Where downloads go unless the user says otherwise.
def downloads_dir() -> Path:
    return data_dir() / "downloads"


@dataclass
class Result:
    """One thing found on YouTube Music — a track, album, artist or playlist."""

    kind: str = "song"           # song | video | album | artist | playlist
    id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: int = 0
    thumbnail: str = ""
    #: For albums and playlists, so they can be opened.
    browse_id: str = ""

    @property
    def url(self) -> str:
        if self.kind in ("song", "video"):
            return f"https://music.youtube.com/watch?v={self.id}"
        return f"https://music.youtube.com/browse/{self.browse_id or self.id}"

    @property
    def clock(self) -> str:
        minutes, seconds = divmod(max(0, self.duration), 60)
        return f"{minutes}:{seconds:02d}"

    @property
    def subtitle(self) -> str:
        return " · ".join(part for part in (self.artist, self.album) if part)


def _duration_seconds(value: Any) -> int:
    """"3:42" or 222 → 222. Anything unreadable → 0."""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value:
        parts = value.split(":")
        try:
            numbers = [int(p) for p in parts]
        except ValueError:
            return 0
        seconds = 0
        for number in numbers:
            seconds = seconds * 60 + number
        return seconds
    return 0


def _artists(entry: dict) -> str:
    artists = entry.get("artists") or entry.get("author")
    if isinstance(artists, list):
        names = [a.get("name", "") for a in artists if isinstance(a, dict)]
        return ", ".join(n for n in names if n)
    if isinstance(artists, str):
        return artists
    return ""


def _thumbnail(entry: dict) -> str:
    thumbnails = entry.get("thumbnails") or entry.get("thumbnail") or []
    if isinstance(thumbnails, dict):
        thumbnails = thumbnails.get("thumbnails", [])
    if isinstance(thumbnails, list) and thumbnails:
        last = thumbnails[-1]
        if isinstance(last, dict):
            return last.get("url", "")
    return ""


def to_result(entry: dict) -> Result:
    """One search or browse row as a `Result`, whatever shape it arrived in."""
    kind = entry.get("resultType") or entry.get("type") or "song"
    album = entry.get("album")
    if isinstance(album, dict):
        album = album.get("name", "")

    return Result(
        kind=str(kind).lower(),
        id=entry.get("videoId") or entry.get("browseId") or entry.get("playlistId") or "",
        title=entry.get("title") or entry.get("name") or "",
        artist=_artists(entry),
        album=album or "",
        duration=_duration_seconds(entry.get("duration_seconds") or entry.get("duration")),
        thumbnail=_thumbnail(entry),
        browse_id=entry.get("browseId") or entry.get("playlistId") or "",
    )


class YouTubeMusic:
    """Browsing and searching. Constructed lazily so a missing library is survivable."""

    def __init__(self, auth_file: Optional[Path] = None) -> None:
        self.auth_file = auth_file
        self._api = None
        self._failed = False

    @property
    def available(self) -> bool:
        return self.api is not None

    @property
    def api(self):
        """The ytmusicapi client, built on first use.

        A failure is remembered rather than retried on every keystroke: without
        it, a machine with no network turns every search box into a stall.
        """
        if self._api is None and not self._failed:
            try:
                from ytmusicapi import YTMusic

                auth = str(self.auth_file) if self.auth_file and Path(self.auth_file).exists() else None
                self._api = YTMusic(auth)
            except Exception as exc:              # noqa: BLE001 — import or network
                logger.warning("YouTube Music is unavailable: %s", exc)
                self._failed = True
        return self._api

    def reset(self) -> None:
        """Try again after a failure — e.g. once the network is back."""
        self._api = None
        self._failed = False

    # ── Reading ───────────────────────────────────────────────────

    def search(self, query: str, kind: Optional[str] = None, limit: int = 25) -> list[Result]:
        if not query.strip() or self.api is None:
            return []
        try:
            rows = self.api.search(query, filter=kind, limit=limit)
        except Exception as exc:                  # noqa: BLE001
            logger.warning("search failed: %s", exc)
            return []
        return [to_result(row) for row in rows if isinstance(row, dict)]

    def home(self, limit: int = 6) -> list[tuple[str, list[Result]]]:
        """The home feed, as (section title, items) — what YT Music opens on."""
        if self.api is None:
            return []
        try:
            sections = self.api.get_home(limit=limit)
        except Exception as exc:                  # noqa: BLE001
            logger.warning("home feed failed: %s", exc)
            return []

        feed = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            contents = section.get("contents") or []
            items = [to_result(c) for c in contents if isinstance(c, dict)]
            if items:
                feed.append((section.get("title", "For you"), items))
        return feed

    def playlist(self, playlist_id: str, limit: int = 200) -> tuple[str, list[Result]]:
        if self.api is None:
            return "", []
        try:
            data = self.api.get_playlist(playlist_id, limit=limit)
        except Exception as exc:                  # noqa: BLE001
            logger.warning("playlist %s failed: %s", playlist_id, exc)
            return "", []
        tracks = [to_result(t) for t in data.get("tracks", []) if isinstance(t, dict)]
        return data.get("title", ""), tracks

    def album(self, browse_id: str) -> tuple[str, list[Result]]:
        if self.api is None:
            return "", []
        try:
            data = self.api.get_album(browse_id)
        except Exception as exc:                  # noqa: BLE001
            logger.warning("album %s failed: %s", browse_id, exc)
            return "", []
        tracks = [to_result(t) for t in data.get("tracks", []) if isinstance(t, dict)]
        return data.get("title", ""), tracks

    def best_match(self, title: str, artist: str = "") -> Optional[Result]:
        """The closest song to a title and artist — what the importers match on."""
        query = f"{artist} {title}".strip()
        for result in self.search(query, kind="songs", limit=5):
            return result
        return None


# ── Downloading ───────────────────────────────────────────────────

@dataclass
class DownloadRequest:
    """One thing to fetch."""

    video_id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    #: mp3 keeps it playable everywhere; opus keeps it small and lossless-ish.
    fmt: str = "mp3"
    quality: str = "0"           # yt-dlp audio quality, 0 is best


@dataclass
class DownloadResult:
    ok: bool = False
    path: str = ""
    error: str = ""
    request: Optional[DownloadRequest] = None


def download(
    request: DownloadRequest,
    folder: Optional[Path] = None,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    cookies: Optional[tuple] = None,
) -> DownloadResult:
    """Fetch one track as audio, tagged, into the downloads folder.

    Files are named `Artist - Title.ext` rather than by video id, because the
    point of downloading is to end up with a music file, not a YouTube artefact.
    """
    if not request.video_id.strip():
        return DownloadResult(error="No video to download", request=request)

    folder = Path(folder or downloads_dir())
    folder.mkdir(parents=True, exist_ok=True)

    try:
        import yt_dlp
    except ImportError:
        return DownloadResult(error="yt-dlp is not installed", request=request)

    stem = _safe_stem(request.artist, request.title) or request.video_id

    # yt-dlp calls the hook for every chunk it reads — several times a second,
    # per download. Passing all of that to the interface is what made a playlist
    # import lock the window up, so it is rate-limited here at the source.
    last_report = [0.0, ""]

    def hook(status: dict) -> None:
        if progress is None:
            return

        state = status.get("status")
        if state == "finished":
            progress(1.0, "converting")
            return
        if state != "downloading":
            return

        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        done = status.get("downloaded_bytes") or 0
        fraction = (done / total) if total else 0.0

        now = time.monotonic()
        moved = abs(fraction - last_report[0]) >= 0.02
        waited = now - (last_report[1] or 0) >= PROGRESS_INTERVAL if last_report[1] else True

        if moved or waited:
            last_report[0] = fraction
            last_report[1] = now
            progress(fraction, "downloading")

    options = {
        "format": FORMAT,
        "extractor_args": {"youtube": {"player_client": PLAYER_CLIENTS}},
        "outtmpl": str(folder / f"{stem}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": request.fmt,
                "preferredquality": request.quality,
            },
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ],
        "writethumbnail": True,
        "postprocessor_args": {"embedthumbnail+ffmpeg_o": ["-c:v", "mjpeg"]},
    }

    if cookies:
        options["cookiesfrombrowser"] = cookies

    url = f"https://music.youtube.com/watch?v={request.video_id}"

    def fetch(with_options) -> None:
        with yt_dlp.YoutubeDL(with_options) as downloader:
            downloader.extract_info(url, download=True)

    try:
        fetch(options)
    except Exception as exc:                      # noqa: BLE001 — yt-dlp raises widely
        # An age wall is not a refusal to serve the song, it is a request to
        # say who is asking — and you are already signed in, in the browser
        # on this machine. Passing that on and trying once more is the whole
        # of it. Reported as "cannot download this" instead, it read as the
        # app deciding what you are allowed to listen to.
        retry = None if cookies else browser_cookies()
        if retry is None or not is_a_sign_in_wall(str(exc)):
            return DownloadResult(error=str(exc), request=request)

        logger.info("%s is behind a sign-in; asking again with your browser's "
                    "session", request.video_id)
        options["cookiesfrombrowser"] = retry
        try:
            fetch(options)
        except Exception as second:               # noqa: BLE001
            return DownloadResult(error=str(second), request=request)

    final = folder / f"{stem}.{request.fmt}"
    if not final.exists():
        # The postprocessor may have chosen a different container.
        candidates = sorted(folder.glob(f"{stem}.*"))
        audio = [c for c in candidates if c.suffix.lower() not in (".jpg", ".png", ".webp", ".part")]
        if not audio:
            return DownloadResult(error="the download produced no audio file", request=request)
        final = audio[0]

    _write_tags(final, request)
    return DownloadResult(ok=True, path=str(final), request=request)


def _safe_stem(artist: str, title: str) -> str:
    name = f"{artist} - {title}".strip(" -") if artist else title.strip()
    for character in '/\\:*?"<>|':
        name = name.replace(character, "")
    return name.strip()[:120]


def _write_tags(path: Path, request: DownloadRequest) -> None:
    """Make sure the tags say what we asked for, whatever YouTube called it."""
    try:
        import mutagen

        audio = mutagen.File(path, easy=True)
        if audio is None:
            return
        if request.title:
            audio["title"] = request.title
        if request.artist:
            audio["artist"] = request.artist
        if request.album:
            audio["album"] = request.album
        audio.save()
    except Exception as exc:                      # noqa: BLE001
        logger.debug("could not write tags to %s: %s", path, exc)


def track_from_download(result: DownloadResult) -> Optional[Track]:
    """The downloaded file as a library track."""
    if not result.ok or not result.path:
        return None

    from rose_bouquet.core.library import read_track

    track = read_track(Path(result.path))
    track.source = "youtube"
    if result.request is not None:
        track.source_id = result.request.video_id
        track.title = track.title or result.request.title
        track.artist = track.artist or result.request.artist
        track.album = track.album or result.request.album
    return track
