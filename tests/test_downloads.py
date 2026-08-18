"""Which YouTube client the downloader asks, and why it matters.

This is a quality test disguised as a configuration test. Signed out, YouTube
does not offer every client the same formats: `tv`, `android`, `ios` and `web`
are given no audio-only stream at all, so asking them for `bestaudio` silently
falls through to itag 18 — a muxed 360p MP4 carrying about 96 kbps of AAC. The
download works. It is just eleven megabytes of video, thrown away, to end up
with the worst audio on the platform.

`web_embedded`, `tv_embedded`, `android_vr` and the music clients are offered
itag 251 (129 kbps Opus) and 140 (129 kbps AAC) with no account at all.
Measured on one track: 3.3 MB instead of 11.3 MB, from a better source.

Nothing in the app's behaviour makes that ordering obvious, and getting it
wrong costs quality rather than raising an error, which is exactly the sort of
regression that survives a release. Hence a test that says it out loud.

The download list and the streaming list disagree about `web_embedded`, and
that is deliberate rather than an oversight — the test below pins it so it
does not get "fixed" into agreement.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from rose_bouquet.core import ytmusic

#: Clients that serve a signed-out request no audio-only format whatsoever.
MUXED_ONLY = {"tv", "android", "ios", "web", "mweb", "tv_simply", "web_music"}

#: Clients measured serving adaptive audio, signed out, on 2026-08-17.
SERVES_AUDIO = {"ios_music", "android_music", "android_vr", "tv_embedded",
                "web_embedded"}


def test_the_clients_that_have_good_audio_are_asked_first():
    clients = ytmusic.PLAYER_CLIENTS
    good = [c for c in clients if c in SERVES_AUDIO]
    poor = [c for c in clients if c in MUXED_ONLY]

    assert good, "no client that offers audio-only formats is asked at all"
    assert poor, "the muxed-only clients are still worth keeping as a fallback"
    # Order is the whole point: yt-dlp takes the first format that satisfies
    # the selector, so a muxed-only client asked first wins and the good audio
    # is never seen.
    assert clients.index(good[-1]) < clients.index(poor[0])


def test_streaming_asks_a_client_that_actually_has_audio():
    """Audio-only streaming has to be offered audio, or it streams the video.

    `bestaudio[vcodec=none]` cannot select what the client never listed, so
    this was "audio only" in name while a 360p picture went over the wire.
    """
    from rose_bouquet.core import youtube

    assert set(youtube.STREAM_CLIENTS) <= SERVES_AUDIO
    assert set(youtube.FALLBACK_STREAM_CLIENTS) & SERVES_AUDIO


def test_the_two_lists_disagree_about_web_embedded_on_purpose():
    """The best client for downloading is the worst one for streaming.

    yt-dlp fetches a URL inside the session that minted it, so `web_embedded`
    is the most reliable download client there is — 4/4 where the others miss.
    Streaming has no session: Qt fetches the URL cold, and `web_embedded`
    answered 403 to that every single time. Making the lists agree would break
    one end or the other, so this says which way round it goes.
    """
    from rose_bouquet.core import youtube

    assert "web_embedded" in ytmusic.PLAYER_CLIENTS
    assert "web_embedded" not in youtube.STREAM_CLIENTS
    assert "web_embedded" not in youtube.FALLBACK_STREAM_CLIENTS


def test_the_downloader_hands_yt_dlp_that_list(tmp_path, monkeypatch):
    """The constants above are only worth testing if they reach yt-dlp."""
    seen = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            seen.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, url, download=False):
            # Stand in for the real thing having written a file.
            (tmp_path / "Someone - A Song.mp3").write_bytes(b"not really audio")
            return {}

    monkeypatch.setitem(sys.modules, "yt_dlp",
                        types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    result = ytmusic.download(
        ytmusic.DownloadRequest(video_id="abc123", title="A Song",
                                artist="Someone"),
        tmp_path,
    )

    assert result.ok, result.error
    assert seen["format"] == ytmusic.FORMAT
    assert seen["extractor_args"]["youtube"]["player_client"] == ytmusic.PLAYER_CLIENTS


# ── An age wall is a question, not a refusal ──────────────────────

def test_a_sign_in_wall_is_told_apart_from_a_real_failure():
    """YouTube says "prove who you are" in several different sentences.

    All of them are answered by the same thing — the session already in the
    browser on this machine — and none of the others are, so the app must not
    go rummaging in a cookie jar every time a download fails for an ordinary
    reason like a dead video or a full disk.
    """
    from rose_bouquet.core.ytmusic import is_a_sign_in_wall

    walls = [
        "ERROR: [youtube] x: Sign in to confirm your age. This video may be "
        "inappropriate for some users.",
        "ERROR: Sign in to confirm you’re not a bot",
        "ERROR: Sign in to confirm you're not a bot",
        "ERROR: This video is private",
        "ERROR: Join this channel to get access to members-only content",
        "ERROR: Sign in to view this video",
    ]
    for message in walls:
        assert is_a_sign_in_wall(message), message

    ordinary = [
        "ERROR: Video unavailable",
        "ERROR: HTTP Error 403: Forbidden",
        "ERROR: unable to write data: No space left on device",
        "ERROR: Unsupported URL",
        "ERROR: Postprocessing: ffmpeg not found",
        "",
    ]
    for message in ordinary:
        assert not is_a_sign_in_wall(message), message


def test_an_age_walled_download_is_asked_for_again_with_your_session(tmp_path, monkeypatch):
    """The retry happens once, and only for the failure it is meant for."""
    from rose_bouquet.core import ytmusic

    attempts = []

    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, url, download=False):
            attempts.append(self.options.get("cookiesfrombrowser"))
            if self.options.get("cookiesfrombrowser") is None:
                raise RuntimeError("ERROR: Sign in to confirm your age.")
            (tmp_path / "Somebody - A song.mp3").write_bytes(b"audio")
            return {"title": "A song"}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(ytmusic, "browser_cookies",
                        lambda: ("firefox", "/a/profile", None, None))
    monkeypatch.setattr(ytmusic, "_write_tags", lambda *a, **k: None)

    request = ytmusic.DownloadRequest(video_id="v", title="A song",
                                      artist="Somebody", album="", fmt="mp3")
    result = ytmusic.download(request, folder=tmp_path, progress=lambda *a: None)

    assert result.ok, result.error
    # Tried without a session first, then once with it. Not more.
    assert attempts == [None, ("firefox", "/a/profile", None, None)]


def test_an_ordinary_failure_does_not_go_looking_for_cookies(tmp_path, monkeypatch):
    from rose_bouquet.core import ytmusic

    attempts = []

    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, url, download=False):
            attempts.append(self.options.get("cookiesfrombrowser"))
            raise RuntimeError("ERROR: Video unavailable")

    asked = []
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(ytmusic, "browser_cookies",
                        lambda: asked.append(True) or ("firefox", "/p", None, None))

    request = ytmusic.DownloadRequest(video_id="v", title="A song",
                                      artist="Somebody", album="", fmt="mp3")
    result = ytmusic.download(request, folder=tmp_path, progress=lambda *a: None)

    assert not result.ok
    assert "Video unavailable" in result.error
    assert attempts == [None]          # asked once, and not asked again
