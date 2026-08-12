"""Serving the library to other devices on the network.

Two APIs over one small HTTP server, because they answer different needs:

  - **A Subsonic-compatible API.** This is the important one. Subsonic's API is
    what every third-party music client speaks — Symfonium, DSub, substreamer,
    Feishin, play:Sub — so implementing it means the library is playable from
    devices this project will never write an app for. Rose Bouquet's own Android
    client speaks it too, rather than inventing a private protocol that only
    ever has one implementation.
  - **A small JSON API** for the things Subsonic has no concept of: the queue
    the desktop is currently playing, and remote control of it.

It is deliberately a *LAN* server. It binds to the local network, authenticates
with a password the user sets, and has no notion of being on the open internet.
Anyone wanting that should put it behind a reverse proxy they trust, and that is
said plainly in the settings rather than implied by a checkbox.

Streaming is by byte range, so a client can seek without downloading the whole
file first, and transcoding is not attempted at all: the files are already in
formats phones play, and a music server that pins a CPU core to re-encode what
was fine to begin with is a bad trade.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import secrets
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

DEFAULT_PORT = 4533          # the port Navidrome uses; clients often default to it
SUBSONIC_VERSION = "1.16.1"
SERVER_NAME = "rose-bouquet"

#: How much of a file to send per chunk. Big enough to be efficient, small
#: enough that a seek does not sit behind a megabyte already in flight.
CHUNK = 64 * 1024


def local_ip() -> str:
    """The address other devices on the network should use.

    Found by opening a UDP socket to somewhere unroutable — no packet is sent,
    but the kernel picks the interface it would use, which is the one worth
    telling people about.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


@dataclass
class ServerConfig:
    """How the server is exposed."""

    enabled: bool = False
    port: int = DEFAULT_PORT
    #: Empty means "anyone on the network", which is only ever right at home.
    password: str = ""
    username: str = "rose"
    #: 0.0.0.0 serves the whole LAN; 127.0.0.1 keeps it on this machine.
    host: str = "0.0.0.0"

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled, "port": self.port,
            "password": self.password, "username": self.username, "host": self.host,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "ServerConfig":
        if not isinstance(data, dict):
            return cls()
        config = cls()
        config.enabled = bool(data.get("enabled"))
        config.username = str(data.get("username") or "rose")
        config.password = str(data.get("password") or "")
        config.host = str(data.get("host") or "0.0.0.0")
        try:
            config.port = max(1, min(65535, int(data.get("port", DEFAULT_PORT))))
        except (TypeError, ValueError):
            config.port = DEFAULT_PORT
        return config

    @property
    def url(self) -> str:
        return f"http://{local_ip()}:{self.port}"


def new_password() -> str:
    """A short, readable, actually-random password to suggest."""
    return secrets.token_urlsafe(9)


def check_token(password: str, token: str, salt: str) -> bool:
    """Subsonic's token auth: md5(password + salt), compared without leaking timing.

    md5 is not a defensible choice in 2026, but it is what the protocol
    specifies and what every client sends. The mitigation that matters is the
    one above: this is a LAN server, and the settings screen says so.
    """
    expected = hashlib.md5(f"{password}{salt}".encode()).hexdigest()   # noqa: S324
    return secrets.compare_digest(expected, (token or "").lower())


@dataclass
class MusicServer:
    """A threaded HTTP server over a library and the running player."""

    library: Any
    config: ServerConfig = field(default_factory=ServerConfig)
    #: Called for remote control. Given (action, argument); returns anything.
    control: Optional[Callable[[str, str], Any]] = None
    #: Returns what is playing now, as a dict.
    now_playing: Optional[Callable[[], dict]] = None

    _server: Optional[ThreadingHTTPServer] = None
    _thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> tuple[bool, str]:
        """Start serving. Returns (ok, message) rather than raising."""
        if self.running:
            return True, f"Already serving on {self.config.url}"

        handler = _make_handler(self)
        try:
            self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler)
        except OSError as exc:
            self._server = None
            return False, f"Could not listen on port {self.config.port}: {exc}"

        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="rose-bouquet-server", daemon=True)
        self._thread.start()
        logger.info("serving on %s", self.config.url)
        return True, f"Serving on {self.config.url}"

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def restart(self) -> tuple[bool, str]:
        self.stop()
        return self.start()

    # ── Authentication ────────────────────────────────────────────

    def authorised(self, params: dict[str, list[str]]) -> bool:
        if not self.config.password:
            return True

        password = _one(params, "p")
        if password.startswith("enc:"):
            try:
                password = bytes.fromhex(password[4:]).decode("utf-8", "replace")
            except ValueError:
                password = ""
        if password and secrets.compare_digest(password, self.config.password):
            return True

        return check_token(self.config.password, _one(params, "t"), _one(params, "s"))

    # ── The data clients ask for ──────────────────────────────────

    def tracks(self) -> list:
        return self.library.all()

    def track_by_id(self, track_id: str) -> Optional[Any]:
        for track in self.library.tracks.values():
            if _track_id(track) == track_id:
                return track
        return None

    def albums(self) -> list[tuple[str, str, list]]:
        return [(artist, album, tracks)
                for (artist, album), tracks in self.library.albums().items()]


def _track_id(track) -> str:
    """A stable id for a track, derived from its path.

    Derived rather than stored so the library file stays a plain cache of the
    filesystem — and so two machines serving the same folder agree on ids.
    """
    return hashlib.sha1(track.path.encode("utf-8")).hexdigest()[:16]   # noqa: S324


def _album_id(artist: str, album: str) -> str:
    return hashlib.sha1(f"{artist}::{album}".encode()).hexdigest()[:16]  # noqa: S324


def _one(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key)
    return values[0] if values else default


# ── The HTTP layer ────────────────────────────────────────────────

def _make_handler(server: MusicServer):
    """Build a handler class bound to one server instance."""

    class Handler(BaseHTTPRequestHandler):
        server_version = f"RoseBouquet/{SUBSONIC_VERSION}"
        protocol_version = "HTTP/1.1"

        # The default logs every request to stderr; a music player streaming a
        # track would fill a terminal with noise.
        def log_message(self, fmt: str, *args) -> None:
            logger.debug("%s - %s", self.address_string(), fmt % args)

        # ── Routing ───────────────────────────────────────────────

        def do_GET(self) -> None:            # noqa: N802 — http.server's name
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            route = parsed.path.rstrip("/")

            if route in ("", "/"):
                return self._send_json({"server": SERVER_NAME, "api": "subsonic",
                                        "version": SUBSONIC_VERSION})

            if route.startswith("/rest/"):
                return self._subsonic(route[len("/rest/"):], params)

            if route.startswith("/api/"):
                return self._json_api(route[len("/api/"):], params)

            self._send_error(404, "Not found")

        def do_HEAD(self) -> None:           # noqa: N802
            self.do_GET()

        # ── Subsonic ──────────────────────────────────────────────

        def _subsonic(self, action: str, params: dict) -> None:
            action = action.removesuffix(".view")

            if not server.authorised(params):
                return self._subsonic_response(
                    {"error": {"code": 40, "message": "Wrong username or password"}},
                    params, status="failed",
                )

            if action == "ping":
                return self._subsonic_response({}, params)

            if action == "getLicense":
                return self._subsonic_response(
                    {"license": {"valid": True}}, params)

            if action in ("getMusicFolders",):
                return self._subsonic_response({"musicFolders": {"musicFolder": [
                    {"id": 1, "name": "Rose Bouquet"}]}}, params)

            if action in ("getArtists", "getIndexes"):
                return self._subsonic_response(self._artists_payload(), params)

            if action == "getAlbumList2" or action == "getAlbumList":
                key = "albumList2" if action == "getAlbumList2" else "albumList"
                return self._subsonic_response({key: {"album": self._albums_payload()}}, params)

            if action == "getAlbum":
                return self._subsonic_response(self._album_payload(_one(params, "id")), params)

            if action == "getSong":
                track = server.track_by_id(_one(params, "id"))
                if track is None:
                    return self._subsonic_response(
                        {"error": {"code": 70, "message": "Song not found"}},
                        params, status="failed")
                return self._subsonic_response({"song": _song(track)}, params)

            if action in ("search3", "search2"):
                query = _one(params, "query")
                found = server.library.search(query)[:100]
                key = "searchResult3" if action == "search3" else "searchResult2"
                return self._subsonic_response(
                    {key: {"song": [_song(t) for t in found]}}, params)

            if action == "getRandomSongs":
                import random

                tracks = list(server.tracks())
                random.shuffle(tracks)
                size = int(_one(params, "size", "20") or 20)
                return self._subsonic_response(
                    {"randomSongs": {"song": [_song(t) for t in tracks[:size]]}}, params)

            if action in ("stream", "download"):
                return self._stream(_one(params, "id"))

            if action in ("getCoverArt",):
                return self._cover(_one(params, "id"))

            if action == "scrobble":
                return self._subsonic_response({}, params)

            # An unknown method is answered with Subsonic's own error code
            # rather than a 404, so clients report it usefully.
            return self._subsonic_response(
                {"error": {"code": 0, "message": f"Unsupported method {action}"}},
                params, status="failed")

        def _artists_payload(self) -> dict:
            index: dict[str, list] = {}
            for artist, tracks in server.library.artists().items():
                letter = (artist[:1] or "#").upper()
                index.setdefault(letter, []).append({
                    "id": _album_id(artist, ""),
                    "name": artist,
                    "albumCount": len({t.display_album for t in tracks}),
                })

            return {"artists": {"index": [
                {"name": letter, "artist": artists}
                for letter, artists in sorted(index.items())
            ]}}

        def _albums_payload(self) -> list[dict]:
            payload = []
            for artist, album, tracks in server.albums():
                payload.append({
                    "id": _album_id(artist, album),
                    "name": album,
                    "artist": artist,
                    "songCount": len(tracks),
                    "duration": sum(t.duration for t in tracks),
                    "coverArt": _track_id(tracks[0]) if tracks else "",
                    "year": tracks[0].year if tracks and tracks[0].year.isdigit() else None,
                })
            return payload

        def _album_payload(self, album_id: str) -> dict:
            for artist, album, tracks in server.albums():
                if _album_id(artist, album) == album_id:
                    return {"album": {
                        "id": album_id, "name": album, "artist": artist,
                        "songCount": len(tracks),
                        "duration": sum(t.duration for t in tracks),
                        "song": [_song(t) for t in tracks],
                    }}
            return {"error": {"code": 70, "message": "Album not found"}}

        # ── Rose Bouquet's own API ──────────────────────────────────

        def _json_api(self, action: str, params: dict) -> None:
            if not server.authorised(params):
                return self._send_json({"error": "unauthorised"}, status=401)

            if action == "now-playing":
                payload = server.now_playing() if server.now_playing else {}
                return self._send_json(payload or {})

            if action == "library":
                return self._send_json({"tracks": [
                    {"id": _track_id(t), "title": t.display_title,
                     "artist": t.display_artist, "album": t.display_album,
                     "duration": t.duration}
                    for t in server.tracks()
                ]})

            if action.startswith("control/"):
                if server.control is None:
                    return self._send_json({"error": "remote control is off"}, status=403)
                result = server.control(action[len("control/"):], _one(params, "value"))
                return self._send_json({"ok": True, "result": result})

            self._send_error(404, "Not found")

        # ── Streaming ─────────────────────────────────────────────

        def _stream(self, track_id: str) -> None:
            track = server.track_by_id(track_id)
            if track is None or not Path(track.path).exists():
                return self._send_error(404, "Track not found")

            path = Path(track.path)
            size = path.stat().st_size
            kind = mimetypes.guess_type(path.name)[0] or "audio/mpeg"

            start, end = self._range(size)
            length = end - start + 1
            # Partial whenever a range was asked for — including one starting at
            # byte 0, which is what a client sends to probe for range support.
            # Answering that with a bare 200 is why some clients refuse to seek.
            partial = self.headers.get("Range", "").startswith("bytes=")

            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()

            if self.command == "HEAD":
                return

            try:
                with path.open("rb") as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = handle.read(min(CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                # The client seeked or moved on. Entirely normal; not an error.
                logger.debug("stream closed early for %s", track.display_title)

        def _range(self, size: int) -> tuple[int, int]:
            header = self.headers.get("Range", "")
            if not header.startswith("bytes="):
                return 0, size - 1

            span = header[len("bytes="):].split(",")[0]
            first, _, last = span.partition("-")
            try:
                start = int(first) if first else 0
                end = int(last) if last else size - 1
            except ValueError:
                return 0, size - 1

            start = max(0, min(start, size - 1))
            end = max(start, min(end, size - 1))
            return start, end

        def _cover(self, cover_id: str) -> None:
            track = server.track_by_id(cover_id)
            if track is None or not track.cover or not Path(track.cover).exists():
                return self._send_error(404, "No cover art")

            data = Path(track.cover).read_bytes()
            kind = mimetypes.guess_type(track.cover)[0] or "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        # ── Responding ────────────────────────────────────────────

        def _subsonic_response(self, payload: dict, params: dict, *, status: str = "ok") -> None:
            body = {"subsonic-response": {
                "status": status, "version": SUBSONIC_VERSION,
                "type": SERVER_NAME, "serverVersion": "0.1.0", "openSubsonic": True,
                **payload,
            }}

            if _one(params, "f") == "jsonp" and _one(params, "callback"):
                text = f"{_one(params, 'callback')}({json.dumps(body)});"
                return self._send_raw(text.encode(), "application/javascript")

            self._send_json(body)

        def _send_json(self, payload: dict, status: int = 200) -> None:
            self._send_raw(json.dumps(payload).encode(), "application/json", status)

        def _send_raw(self, data: bytes, kind: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _send_error(self, status: int, message: str) -> None:
            self._send_json({"error": message}, status=status)

    return Handler


def _song(track) -> dict:
    """One track in the shape Subsonic clients expect."""
    return {
        "id": _track_id(track),
        "parent": _album_id(track.album_artist or track.display_artist, track.display_album),
        "title": track.display_title,
        "album": track.display_album,
        "artist": track.display_artist,
        "albumArtist": track.album_artist or track.display_artist,
        "track": track.track_number or None,
        "discNumber": track.disc_number or None,
        "year": int(track.year) if track.year.isdigit() else None,
        "genre": track.genre or None,
        "coverArt": _track_id(track),
        "size": Path(track.path).stat().st_size if Path(track.path).exists() else 0,
        "contentType": mimetypes.guess_type(track.path)[0] or "audio/mpeg",
        "suffix": Path(track.path).suffix.lstrip("."),
        "duration": track.duration,
        "playCount": track.play_count,
        "isDir": False,
        "type": "music",
    }
