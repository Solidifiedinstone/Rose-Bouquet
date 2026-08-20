"""What the YouTube tab refuses to send.

The tab's whole claim is that it is YouTube without the client that reports on
you, and that claim lives in one class: the request interceptor. Everything
else about the tab — the injected CSS, the skip script — is cosmetic and
expected to rot when Google renames a class next week. This is the layer that
is supposed to hold, so it is the layer with a test.

Two halves, and the second matters as much as the first. A blocklist that
takes ads down is easy; one that also takes sign-in, the video itself or the
thumbnails down is a broken app that happens to be private. So every case here
is paired: what must never leave the machine, and what must always be allowed
through.
"""

from __future__ import annotations

import pytest

# Running offscreen is `conftest.py`'s job — it has to be set before Qt is
# imported anywhere, and doing it per-file meant doing it too late.


#: Ads and beacons. Nothing here has any business being requested.
BLOCKED = [
    # The modern watch-and-report endpoint, called constantly while you browse.
    "https://www.youtube.com/youtubei/v1/log_event?alt=json",
    # YouTube's own beacon host, which serves nothing else.
    "https://s.youtube.com/api/stats/watchtime?ns=yt",
    "https://www.youtube.com/api/stats/ads?ver=2",
    "https://www.youtube.com/api/stats/qoe?event=streamingstats",
    "https://www.youtube.com/api/stats/atr?ns=yt",
    "https://www.youtube.com/ptracking?video_id=abc",
    "https://www.youtube.com/pagead/interaction/?ai=abc",
    # The empty-response beacons — a 204 is a reply nobody reads, which is
    # what tells you the request existed only to be counted.
    "https://www.youtube.com/generate_204",
    "https://www.youtube.com/gen_204?atyp=i",
    "https://www.youtube.com/csi_204?v=abc",
    "https://www.youtube.com/error_204?t=x",
    # Ad networks, including a subdomain, which the registrable-domain match
    # is what catches.
    "https://googleads.g.doubleclick.net/pagead/id",
    "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",
    "https://static.doubleclick.net/instream/ad_status.js",
    "https://www.google-analytics.com/collect",
    "https://www.googletagmanager.com/gtag/js",
]

#: The app, working. Every one of these is something breaking if it is blocked.
ALLOWED = [
    # Signing in. The point of allowing a sign-in is that it works.
    "https://accounts.google.com/ServiceLogin",
    "https://accounts.google.com/o/oauth2/auth",
    # The site's own API: the feed, the search, the player.
    "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false",
    "https://www.youtube.com/youtubei/v1/player",
    "https://music.youtube.com/youtubei/v1/next",
    # The video itself, and the pictures.
    "https://rr3---sn-abc.googlevideo.com/videoplayback?expire=1",
    "https://i.ytimg.com/vi/abc/hqdefault.jpg",
    "https://yt3.ggpht.com/avatar.jpg",
    # Deliberately allowed: this is what tells a signed-in account where you
    # got to in a video, which is the resume-where-you-left-off that signing
    # in is for. Blocking it would be privacy nobody asked for.
    "https://www.youtube.com/api/stats/watchtime?ns=yt",
    # Not doubleclick.net. A substring test would have blocked this.
    "https://notdoubleclick.net.example.com/x",
]


class _Request:
    """The bit of Qt's request info the interceptor actually touches."""

    def __init__(self, url: str) -> None:
        from PySide6.QtCore import QUrl

        self._url = QUrl(url)
        self.blocked = False

    def requestUrl(self):                        # noqa: N802 (Qt's name)
        return self._url

    def block(self, on: bool) -> None:
        self.blocked = on


def _verdict(url: str) -> bool:
    from rose_bouquet.ui.youtube_tab import AdBlocker

    request = _Request(url)
    AdBlocker().interceptRequest(request)
    return request.blocked


@pytest.mark.parametrize("url", BLOCKED)
def test_ads_and_telemetry_never_leave_the_machine(url):
    assert _verdict(url), f"{url} was allowed through"


@pytest.mark.parametrize("url", ALLOWED)
def test_signing_in_and_watching_still_work(url):
    assert not _verdict(url), f"{url} was blocked"


def test_it_counts_what_it_stopped():
    """The interface shows a number, so the number has to be real."""
    from rose_bouquet.ui.youtube_tab import AdBlocker

    blocker = AdBlocker()
    for url in BLOCKED[:3] + ALLOWED[:2]:
        blocker.interceptRequest(_Request(url))

    assert blocker.blocked == 3


def test_the_tab_asks_for_the_desktop_site():
    """The left rail only exists on the desktop site, served to a desktop UA."""
    from rose_bouquet.ui import youtube_tab

    assert youtube_tab.WATCH_URL == "https://www.youtube.com"
    assert "Mobile" not in youtube_tab.USER_AGENT
    # Announcing itself as an embedded view is what Google refuses to sign in.
    assert "QtWebEngine" not in youtube_tab.USER_AGENT


# ── The injected stylesheet, and what silently kills it ───────────

def test_the_stylesheet_survives_whatever_is_written_in_it():
    """A backtick in the CSS used to throw away every rule in it.

    The stylesheet is pasted into a JavaScript string to be injected. It used
    to be a template literal, so a backtick or a `${` anywhere in the CSS —
    including in a comment, which is how it actually happened — ended the
    literal early, made the script a syntax error, and dropped the entire
    stylesheet with nothing on screen to say so: no ad hiding, and no room made
    for the bottom bar.
    """
    import json

    from rose_bouquet.ui import youtube_tab

    # Whatever it contains, it goes in as data rather than as source.
    hostile = "/* ` ${x} \\ \" ' */ body { color: red; }"
    encoded = json.dumps(hostile)
    assert encoded.startswith('"') and encoded.endswith('"')
    assert "`" not in encoded.strip('"') or "\\" in encoded
    assert json.loads(encoded) == hostile

    # And the real one round-trips too.
    assert json.loads(json.dumps(youtube_tab.HIDE_CSS)) == youtube_tab.HIDE_CSS


def test_youtubes_menus_are_put_above_our_bottom_bar():
    """Otherwise a menu near the bottom loses its last item to a black band.

    Our bar is fixed at the top of the stacking order; YouTube's popup
    container has no z-index of its own, and an automatic one stacks by
    document order — which a fixed bar wins however modest its number. So the
    menus are raised explicitly rather than the bar lowered.
    """
    from rose_bouquet.ui import youtube_tab

    css = youtube_tab.HIDE_CSS
    for selector in ("ytd-popup-container", "tp-yt-iron-dropdown",
                     "tp-yt-paper-dialog", "ytd-menu-popup-renderer"):
        assert selector in css, selector

    # Above the bar, whatever the bar is set to.
    bar_z = 2147483000
    assert f"z-index: {bar_z + 2}" in css or "2147483002" in css
