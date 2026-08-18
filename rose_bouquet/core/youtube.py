"""Turning a YouTube video id into something a player can open.

This used to be half a YouTube client: channels, uploads, searches, a
subscription box and a `discover` that pooled searches into a feed. All of it
is gone. Browsing is `core/innertube.py` now — YouTube's own API, drawn as
widgets by `ui/youtube_native.py` — so nothing here reimplements a page or
guesses at an algorithm.

What is left is the one job neither of those does: resolve a video id to a
direct media URL, so something found while browsing can be played by the app's
own player. That is also what makes the tab ad-free, since an ad break is
something a player is told to insert and this player is ours.

Audio-only is the default, because this is a music app: a five minute song
should not stream a five minute video to play it.
"""

from __future__ import annotations

import logging
from typing import Optional

#: Shared with the downloader rather than copied from it: the reasoning behind
#: the order is long, measured, and only worth keeping in one place.
from rose_bouquet.core.ytmusic import PLAYER_CLIENTS, is_a_sign_in_wall

logger = logging.getLogger(__name__)

#: Clients used when the URL is handed to *something else* to fetch — which is
#: what streaming is: Qt opens the URL itself, with none of yt-dlp's session.
#:
#: This is a different problem from downloading, where yt-dlp does the fetching
#: and its own session makes any client's URL work. Here a URL has to survive a
#: plain GET from Qt, and two separate things can go wrong with one.
#:
#: **It has to exist.** `android`, `tv`, `ios`, `web` and `mweb` offer a
#: signed-out request no audio-only format whatsoever — see the table in
#: `ytmusic.PLAYER_CLIENTS`. Asking `android` for `bestaudio[vcodec=none]` was
#: therefore asking for something it never had, and "audio only" quietly
#: streamed a 360p video instead.
#:
#: **It has to be served to us.** Measured over four videos on 2026-08-17, by
#: fetching the audio URL each client handed back: `android_vr`, `ios_music`,
#: `android_music` and `tv_embedded` served it every time; `web_embedded`
#: refused every time, 4/4, with a 403.
#:
#: That last one is worth pausing on, because it is the exact opposite of what
#: the downloader wants. `web_embedded` is the *most* reliable client there —
#: see `ytmusic.PLAYER_CLIENTS` — since yt-dlp fetches with the session it
#: built the URL in. Streaming has no such session: Qt fetches the URL cold.
#: So the two lists disagree on purpose, and neither is a typo for the other.
#:
#: Asked in tiers rather than all at once. yt-dlp queries every client it is
#: given, in turn, whether or not the first already worked, so the fast one
#: that almost always answers goes on its own first — `android_vr` at 1.3s
#: against several seconds for the whole list — and the rest are there for the
#: times it does not.
STREAM_CLIENTS = ["android_vr"]
FALLBACK_STREAM_CLIENTS = ["ios_music", "android_music", "tv_embedded",
                           "android", "web_safari"]

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
        # No cookies and no login — the client list is what makes that work,
        # and which clients those are is `ytmusic.PLAYER_CLIENTS`.
        "extractor_args": {
            "youtubetab": {"skip": ["authcheck"]},
            "youtube": {"player_client": PLAYER_CLIENTS},
        },
    }
    options.update(extra or {})
    return options


class YouTube:
    """Stream URLs, read from public YouTube without an account."""

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
            if is_a_sign_in_wall(str(exc)):
                signed_in = self._with_your_session(url, options)
                if signed_in is not None:
                    return signed_in
            logger.warning("could not read %s: %s", url, exc)
            return None

    def _with_your_session(self, url: str, options: Optional[dict]) -> Optional[dict]:
        """Ask again as the browser you are already signed in with.

        An age wall is not YouTube declining to serve a song; it is YouTube
        asking who wants it. Answering with the session already on this
        machine is the difference between a track that plays and one the app
        appears to have decided you may not hear.
        """
        import yt_dlp

        from rose_bouquet.core.ytmusic import browser_cookies

        jar = browser_cookies()
        if jar is None:
            return None

        signed_in = _options(options)
        signed_in["cookiesfrombrowser"] = jar
        try:
            with yt_dlp.YoutubeDL(signed_in) as extractor:
                data = extractor.extract_info(url, download=False)
        except Exception as exc:                  # noqa: BLE001
            logger.warning("still refused with your session: %s", exc)
            return None

        logger.info("%s needed a sign-in; used your browser's session", url)
        return data

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
