"""YouTube's own API, spoken directly, with no browser in the middle.

This is what replaces the embedded Chromium. The web view worked, but it was a
whole browser engine sitting in the process — a few hundred megabytes of RAM to
show a grid of thumbnails — and Google refuses to sign you in from one anyway
("this browser or app may not be secure"), which cost the account the whole
point of allowing a sign-in.

So the app talks to InnerTube, which is the private API YouTube's own clients
use: the website, the phone apps and the smart-TV app all speak it. Nothing
here is scraped and no HTML is parsed. Two clients are used, and which one
depends on whether you are signed in:

* **Signed out** — the `WEB` client, the same one youtube.com uses. Search and
  public listings work; the home feed does not, because an anonymous home feed
  is not a thing YouTube has.
* **Signed in** — the `TVHTML5` client, because that is the only client whose
  requests an OAuth token is accepted for. It is also the reason signing in
  works at all: a television cannot open a login page, so Google gives it the
  *device code* flow, where the app shows a short code and you type it into
  google.com/device on a phone or a laptop. No embedded browser, and nothing
  that trips the "may not be secure" check, because it is the flow Google
  designed for exactly this situation.

The credentials below are the YouTube-on-TV app's own, published in every
device on the market and used by `yt-dlp` and `ytmusicapi` for the same reason.
They identify the *client*, not you.

**On telemetry:** every request here is one the app makes deliberately, to
youtube.com, because something on screen needs the answer. There is no
`log_event`, no beacon, no analytics, and no third party — those exist in
YouTube's web page, and this does not load YouTube's web page. Ads are not
blocked so much as absent: an ad break is something the *player* is told to
insert, and the app resolves a media URL and plays it.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from rose_bouquet.core.library import data_dir
from rose_bouquet.core.media import Candidate

logger = logging.getLogger(__name__)

#: The YouTube-on-TV app's credentials. Public knowledge, and they identify the
#: client rather than the user — the same pair `yt-dlp` and `ytmusicapi` use.
CLIENT_ID = "861556708454-d6dlm3lh05idd8npek18k6be8ba3oc68.apps.googleusercontent.com"
CLIENT_SECRET = "SboVhoG9s0rNafixCSGGKXAT"  # noqa: S105 — published, not a secret
SCOPE = "http://gdata.youtube.com https://www.googleapis.com/auth/youtube"

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — a url, not a token
API = "https://www.youtube.com/youtubei/v1"

TIMEOUT = 20

#: Sent as the client identity on every request. The TV one is used when
#: signed in — an OAuth token is only accepted for that client — and the web
#: one when signed out, because its listings parse into more useful shapes.
#:
#: These version strings drift: YouTube dates them, and youtube.com was serving
#: `2.20260817.01.00` when this was last checked. An old one still works — the
#: web version here was two years stale and search answered perfectly well —
#: so this is housekeeping rather than a thing to chase. The live value is in
#: the watch page's `INNERTUBE_CLIENT_VERSION` if it ever needs looking up.
TV_CLIENT = {
    "clientName": "TVHTML5",
    "clientVersion": "7.20250326.16.00",
    "hl": "en",
    "gl": "US",
}
WEB_CLIENT = {
    "clientName": "WEB",
    "clientVersion": "2.20260817.01.00",
    "hl": "en",
    "gl": "US",
}

#: The feeds worth having a button for. These ids are YouTube's own.
#:
#: All three need an account. Measured, not assumed: asked anonymously, home,
#: subscriptions and history all answer with an empty feed, and the ids for
#: Trending and Explore now answer 400 — YouTube retired those surfaces. Search
#: is the only thing that works signed out, which is why the tab says so
#: instead of offering a button that cannot work.
HOME = "FEwhat_to_watch"
SUBSCRIPTIONS = "FEsubscriptions"
HISTORY = "FEhistory"


class YouTubeError(Exception):
    """A request failed in a way the user should be told about plainly."""


class AuthError(YouTubeError):
    """Signing in failed, in a way worth telling the user about."""


@dataclass
class DeviceCode:
    """What to show someone so they can authorise this app on another device."""

    device_code: str = ""
    user_code: str = ""
    verification_url: str = "https://www.google.com/device"
    #: Seconds between polls. Google rejects faster, and says so.
    interval: int = 5
    expires_in: int = 1800


@dataclass
class Tokens:
    access_token: str = ""
    refresh_token: str = ""
    #: Unix time the access token stops working.
    expires_at: float = 0.0

    @property
    def usable(self) -> bool:
        # A minute of margin: a token that expires mid-request is a failure
        # that looks like a bug rather than an expiry.
        return bool(self.access_token) and time.time() < self.expires_at - 60


def _post_form(url: str, fields: dict) -> tuple[int, dict]:
    """A form post to Google's OAuth endpoints."""
    import requests

    response = requests.post(url, data=fields, timeout=TIMEOUT)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {}


class Auth:
    """The sign-in, and the token it leaves behind.

    Kept in the app's own data folder rather than a keyring: there is no
    keyring on every machine this runs on, and a file only this user can read
    is the same guarantee the rest of the app's data already has.
    """

    def __init__(self, path=None) -> None:
        # Coerced rather than required: a string is the obvious thing to pass,
        # and finding out it was wrong meant an AttributeError three frames
        # down inside a read.
        self.path = Path(path) if path else (data_dir() / "youtube-auth.json")
        self.tokens = self._load()

    # ── Where it lives ────────────────────────────────────────────

    def _load(self) -> Tokens:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return Tokens()
        return Tokens(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            expires_at=float(data.get("expires_at", 0) or 0),
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "access_token": self.tokens.access_token,
            "refresh_token": self.tokens.refresh_token,
            "expires_at": self.tokens.expires_at,
        }))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass                            # a filesystem without permissions

    @property
    def signed_in(self) -> bool:
        """Whether there is an account behind this, refreshable or not."""
        return bool(self.tokens.refresh_token or self.tokens.access_token)

    def sign_out(self) -> None:
        """Forget the account. The token is not revoked at Google's end."""
        self.tokens = Tokens()
        try:
            self.path.unlink()
        except OSError:
            pass

    # ── Signing in ────────────────────────────────────────────────

    def start(self) -> DeviceCode:
        """Ask Google for a code to show the user."""
        status, data = _post_form(DEVICE_CODE_URL,
                                  {"client_id": CLIENT_ID, "scope": SCOPE})
        if status != 200 or "device_code" not in data:
            raise AuthError(data.get("error_description")
                            or f"Google would not give us a code ({status})")

        return DeviceCode(
            device_code=data["device_code"],
            user_code=data.get("user_code", ""),
            verification_url=data.get("verification_url")
            or "https://www.google.com/device",
            interval=int(data.get("interval", 5) or 5),
            expires_in=int(data.get("expires_in", 1800) or 1800),
        )

    def poll(self, code: DeviceCode) -> bool:
        """Ask once whether the code has been entered yet.

        Returns True when signed in, False while still waiting. Anything worse
        than waiting raises, because "still waiting" and "you denied it" want
        very different things said to the user.
        """
        status, data = _post_form(TOKEN_URL, {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "device_code": code.device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })

        if status == 200 and data.get("access_token"):
            self._store(data)
            return True

        error = data.get("error", "")
        if error == "slow_down":
            # Google's answer to being polled too fast, and its instruction is
            # to add five seconds and carry on. Written back onto the code so
            # the caller's timer can pick the new interval up; without that the
            # next poll is just as early and gets told off again.
            code.interval += 5
            return False
        if error == "authorization_pending":
            return False
        if error == "expired_token":
            raise AuthError("That code expired. Start again for a new one.")
        if error == "access_denied":
            raise AuthError("Sign-in was refused.")
        raise AuthError(data.get("error_description") or error or "Sign-in failed")

    def _store(self, data: dict) -> None:
        self.tokens = Tokens(
            access_token=data.get("access_token", ""),
            # A refresh only comes back the first time. Keeping the old one
            # rather than blanking it is what stops a refresh from being the
            # thing that signs you out.
            refresh_token=data.get("refresh_token") or self.tokens.refresh_token,
            expires_at=time.time() + float(data.get("expires_in", 3600) or 3600),
        )
        self._save()

    def access_token(self) -> str:
        """A token good right now, refreshing it if it has gone stale."""
        if self.tokens.usable:
            return self.tokens.access_token
        if not self.tokens.refresh_token:
            return ""

        status, data = _post_form(TOKEN_URL, {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": self.tokens.refresh_token,
            "grant_type": "refresh_token",
        })
        if status == 200 and data.get("access_token"):
            self._store(data)
            return self.tokens.access_token

        # A refresh token is good until it is revoked, so a refusal here means
        # the account is gone rather than the network being slow.
        logger.warning("could not refresh the YouTube token: %s", data.get("error"))
        self.sign_out()
        return ""


# ── Reading what comes back ───────────────────────────────────────

def _text(node: Any) -> str:
    """The string out of any of InnerTube's several ways of holding one."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(str(run.get("text", "")) for run in runs
                       if isinstance(run, dict))
    return ""


def _seconds(clock: str) -> int:
    """"12:34" or "1:02:03" as seconds. Anything else is 0."""
    parts = clock.strip().split(":")
    if not parts or not all(part.isdigit() for part in parts):
        return 0
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _thumbnail(node: Any) -> str:
    """The largest thumbnail url anywhere in this node."""
    best, area = "", -1
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if "url" in current and isinstance(current.get("url"), str):
                size = int(current.get("width") or 0) * int(current.get("height") or 0)
                if size > area:
                    best, area = current["url"], size
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return best


def _walk(node: Any, keys: set[str], depth: int = 0) -> Iterator[tuple[str, dict]]:
    """Every renderer of the named kinds, wherever it is nested.

    Walking rather than following exact paths is deliberate. The paths differ
    per client and per surface — a search result, a home shelf and a channel
    page hold the same video in three different places — and they change
    without notice. The renderer names are the stable part.
    """
    if depth > 20:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and isinstance(value, dict):
                yield key, value
            yield from _walk(value, keys, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, keys, depth + 1)


#: The renderers that hold a video, across the clients and surfaces used here.
VIDEO_RENDERERS = {
    "videoRenderer",            # web search
    "gridVideoRenderer",        # web grids
    "compactVideoRenderer",     # web sidebars
    "playlistVideoRenderer",    # inside a playlist
    "richItemRenderer",         # web home, wrapping one of the above
    "tileRenderer",             # everything on the TV client
}


def _from_video_renderer(node: dict) -> Optional[Candidate]:
    video_id = node.get("videoId")
    if not isinstance(video_id, str) or not video_id:
        return None

    channel = (_text(node.get("ownerText"))
               or _text(node.get("longBylineText"))
               or _text(node.get("shortBylineText")))

    # Most renderers spell the length "12:34"; the end-screen one gives a
    # number of seconds and no text at all.
    duration = _seconds(_text(node.get("lengthText")))
    if not duration:
        try:
            duration = int(node.get("lengthInSeconds") or 0)
        except (TypeError, ValueError):
            duration = 0

    return Candidate(
        id=video_id,
        title=_text(node.get("title")),
        artist=channel,
        kind="video",
        published=_text(node.get("publishedTimeText")),
        duration=duration,
        thumbnail=_thumbnail(node.get("thumbnail")),
        source="youtube",
    )


def _from_tile_renderer(node: dict) -> Optional[Candidate]:
    """The TV client's tile, which holds the same facts somewhere else."""
    endpoint = node.get("onSelectCommand") or {}
    watch = endpoint.get("watchEndpoint") or {}
    video_id = watch.get("videoId")
    if not isinstance(video_id, str) or not video_id:
        return None

    #: The title and the channel are in a metadata renderer, which sits in
    #: different places depending on the tile's style.
    title, lines = "", []
    for _name, metadata in _walk(node, {"tileMetadataRenderer"}):
        title = title or _text(metadata.get("title"))
        for _line, line in _walk(metadata.get("lines") or [], {"lineItemRenderer"}):
            text = _text(line.get("text"))
            if text and text != "•":
                lines.append(text)

    clock = ""
    for _name, status in _walk(node, {"thumbnailOverlayTimeStatusRenderer"}):
        clock = _text(status.get("text"))
        break

    return Candidate(
        id=video_id,
        title=title,
        # The lines are "channel • 2 days ago • 1.2M views" in some order; the
        # first is the channel on every tile seen so far.
        artist=lines[0] if lines else "",
        kind="video",
        published=next((line for line in lines[1:] if "ago" in line), ""),
        duration=_seconds(clock),
        thumbnail=_thumbnail(node.get("header") or node),
        source="youtube",
    )


def videos(response: dict) -> list[Candidate]:
    """Every video in a response, in the order it appears, without repeats."""
    found: list[Candidate] = []
    seen: set[str] = set()

    for name, node in _walk(response, VIDEO_RENDERERS):
        if name == "richItemRenderer":
            continue                        # its content is walked in its own right
        item = (_from_tile_renderer(node) if name == "tileRenderer"
                else _from_video_renderer(node))
        if item is None or item.id in seen or not item.title:
            continue
        seen.add(item.id)
        found.append(item)

    return found


def continuation(response: dict) -> str:
    """The token for the next page, or "" when there is no more."""
    for _name, node in _walk(response, {"continuationItemRenderer"}):
        endpoint = node.get("continuationEndpoint") or {}
        command = endpoint.get("continuationCommand") or {}
        token = command.get("token")
        if isinstance(token, str) and token:
            return token

    # The TV client puts it somewhere else entirely.
    for _name, node in _walk(response, {"nextContinuationData"}):
        token = node.get("continuation")
        if isinstance(token, str) and token:
            return token
    return ""


# ── Talking to it ─────────────────────────────────────────────────

@dataclass
class Page:
    """One screenful of results, and how to ask for the next."""

    items: list[Candidate] = field(default_factory=list)
    token: str = ""
    #: Set when the answer was empty for a reason worth showing the user.
    note: str = ""


class InnerTube:
    """YouTube's private API, as much of it as this app needs."""

    def __init__(self, auth: Optional[Auth] = None) -> None:
        self.auth = auth or Auth()

    @property
    def signed_in(self) -> bool:
        return self.auth.signed_in

    def _request(self, endpoint: str, body: dict) -> dict:
        import requests

        token = self.auth.access_token() if self.auth.signed_in else ""
        client = dict(TV_CLIENT if token else WEB_CLIENT)

        headers = {
            "Content-Type": "application/json",
            # The client's own user agent. Not a disguise: this *is* the
            # client the request claims to be.
            "User-Agent": ("Mozilla/5.0 (ChromiumStylePlatform) Cobalt/Version"
                           if token else
                           "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
            "Origin": "https://www.youtube.com",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload = {"context": {"client": client}, **body}

        try:
            response = requests.post(f"{API}/{endpoint}?prettyPrint=false",
                                     json=payload, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise YouTubeError("YouTube is not reachable") from exc

        if response.status_code == 401 and token:
            # The token was refused rather than expired — signing out is
            # honest about it instead of failing every request from here on.
            logger.warning("YouTube refused the account token; signing out")
            self.auth.sign_out()
            raise AuthError("YouTube signed this app out. Sign in again.")

        if response.status_code >= 400:
            # The status is all InnerTube gives; its error bodies are internal
            # and say nothing a user could act on.
            logger.warning("%s answered %s", endpoint, response.status_code)
            raise YouTubeError(f"YouTube refused that request ({response.status_code})")

        try:
            return response.json()
        except ValueError as exc:
            raise YouTubeError("YouTube sent something unreadable") from exc

    # ── The things on screen ──────────────────────────────────────

    def feed(self, browse_id: str = HOME, *, token: str = "") -> Page:
        """A feed by its YouTube id — home, subscriptions, history."""
        if not self.signed_in:
            # Not attempted rather than attempted and empty: an anonymous feed
            # request is a round trip whose answer is always nothing.
            return Page(note="Sign in to see this feed. Search works either way.")

        body = {"continuation": token} if token else {"browseId": browse_id}
        data = self._request("browse", body)
        items = videos(data)

        note = ""
        if not items:
            note = ("YouTube returned nothing for this feed. If you have just "
                    "signed in, it may need a moment to fill.")
        return Page(items=items, token=continuation(data), note=note)

    def search(self, query: str, *, token: str = "") -> Page:
        if not query.strip() and not token:
            return Page()
        body = ({"continuation": token} if token
                else {"query": query.strip()})
        data = self._request("search", body)
        return Page(items=videos(data), token=continuation(data))
