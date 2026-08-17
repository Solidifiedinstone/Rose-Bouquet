"""Speaking YouTube's own API, and keeping the account it gives back.

None of this touches the network. The shapes below are trimmed copies of real
InnerTube responses — a web search result and a TV-client tile — because the
parsing is the part that breaks when YouTube moves something, and a test that
needs YouTube to be up is a test that fails for reasons of its own.

The auth half is here for a different reason: a token file is the one thing in
this app that must not be lost by accident. A refresh that blanks the refresh
token signs the user out for good, silently, and only on the second run.
"""

from __future__ import annotations

import json
import time

import pytest

from rose_bouquet.core import innertube

# ── The small readers ─────────────────────────────────────────────

def test_text_reads_every_shape_innertube_uses():
    assert innertube._text({"simpleText": "Hello"}) == "Hello"
    assert innertube._text({"runs": [{"text": "Hel"}, {"text": "lo"}]}) == "Hello"
    assert innertube._text(None) == ""
    assert innertube._text({}) == ""


@pytest.mark.parametrize("clock, seconds", [
    ("0:42", 42),
    ("12:34", 754),
    ("1:02:03", 3723),
    ("", 0),
    ("LIVE", 0),
    ("Mix", 0),
])
def test_a_clock_becomes_seconds(clock, seconds):
    assert innertube._seconds(clock) == seconds


def test_the_biggest_thumbnail_wins():
    """Small ones come first in the list, so order is not the answer."""
    node = {"thumbnails": [
        {"url": "small.jpg", "width": 120, "height": 90},
        {"url": "big.jpg", "width": 480, "height": 360},
        {"url": "middle.jpg", "width": 320, "height": 180},
    ]}
    assert innertube._thumbnail(node) == "big.jpg"


# ── Real response shapes ──────────────────────────────────────────

WEB_SEARCH = {
    "contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {
        "sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [
            {"videoRenderer": {
                "videoId": "74NluS3jzTo",
                "title": {"runs": [{"text": "Boards of Canada - Introit"}]},
                "ownerText": {"runs": [{"text": "Boards of Canada"}]},
                "lengthText": {"simpleText": "5:47"},
                "publishedTimeText": {"simpleText": "3 months ago"},
                "thumbnail": {"thumbnails": [
                    {"url": "https://i.ytimg.com/vi/74NluS3jzTo/hq.jpg",
                     "width": 480, "height": 360}]},
            }},
            {"videoRenderer": {                       # a second one
                "videoId": "eG26fdvDjX8",
                "title": {"simpleText": "Sleep Mix"},
                "shortBylineText": {"runs": [{"text": "naked flames"}]},
                "lengthText": {"simpleText": "1:00:01"},
            }},
            # Not a video, and must not become one.
            {"channelRenderer": {"channelId": "UCabc", "title": {"simpleText": "A channel"}}},
        ]}}, {"continuationItemRenderer": {
            "continuationEndpoint": {"continuationCommand": {"token": "NEXTPAGE"}}}}]}
    }}},
}

TV_HOME = {
    "contents": {"tvBrowseRenderer": {"content": {"tvSurfaceContentRenderer": {
        "content": {"sectionListRenderer": {"contents": [{"shelfRenderer": {"content": {
            "horizontalListRenderer": {"items": [
                {"tileRenderer": {
                    "onSelectCommand": {"watchEndpoint": {"videoId": "ulj5UJ5GHvE"}},
                    "metadata": {"tileMetadataRenderer": {
                        "title": {"simpleText": "Windowlicker"},
                        "lines": [{"lineRenderer": {"items": [
                            {"lineItemRenderer": {"text": {"simpleText": "Aphex Twin"}}},
                            {"lineItemRenderer": {"text": {"simpleText": "•"}}},
                            {"lineItemRenderer": {"text": {"simpleText": "6 years ago"}}},
                        ]}}],
                    }},
                    "header": {"tileHeaderRenderer": {
                        "thumbnail": {"thumbnails": [
                            {"url": "https://i.ytimg.com/vi/ulj/hq.jpg",
                             "width": 480, "height": 360}]},
                        "thumbnailOverlays": [{"thumbnailOverlayTimeStatusRenderer": {
                            "text": {"simpleText": "6:07"}}}],
                    }},
                }},
            ]}}}}]}},
    }}}},
}


def test_a_web_search_response_becomes_videos():
    found = innertube.videos(WEB_SEARCH)

    assert [item.id for item in found] == ["74NluS3jzTo", "eG26fdvDjX8"]

    first = found[0]
    assert first.title == "Boards of Canada - Introit"
    assert first.artist == "Boards of Canada"
    assert first.duration == 347
    assert first.published == "3 months ago"
    assert first.thumbnail.endswith("hq.jpg")

    # The channel result is not a video, however much it looks like a result.
    assert all(item.kind == "video" for item in found)


def test_the_byline_is_found_wherever_this_renderer_put_it():
    """`ownerText` on one result, `shortBylineText` on the next."""
    assert innertube.videos(WEB_SEARCH)[1].artist == "naked flames"


def test_a_tv_tile_becomes_the_same_thing():
    """The TV client holds every fact somewhere else. Same Candidate out."""
    found = innertube.videos(TV_HOME)

    assert len(found) == 1
    tile = found[0]
    assert tile.id == "ulj5UJ5GHvE"
    assert tile.title == "Windowlicker"
    assert tile.artist == "Aphex Twin"
    assert tile.published == "6 years ago"
    assert tile.duration == 367
    assert tile.thumbnail.endswith("hq.jpg")


def test_the_same_video_twice_is_listed_once():
    doubled = {"a": WEB_SEARCH, "b": WEB_SEARCH}
    assert len(innertube.videos(doubled)) == 2


def test_the_next_page_token_is_found():
    assert innertube.continuation(WEB_SEARCH) == "NEXTPAGE"
    assert innertube.continuation({"nothing": True}) == ""


def test_nothing_at_all_is_not_a_crash():
    assert innertube.videos({}) == []
    assert innertube.videos({"contents": None}) == []


# ── The account ───────────────────────────────────────────────────

def test_a_token_survives_a_restart(tmp_path):
    path = tmp_path / "auth.json"
    auth = innertube.Auth(path)
    auth._store({"access_token": "abc", "refresh_token": "refresh", "expires_in": 3600})

    assert innertube.Auth(path).tokens.access_token == "abc"
    assert innertube.Auth(path).signed_in


def test_only_this_user_can_read_it(tmp_path):
    path = tmp_path / "auth.json"
    auth = innertube.Auth(path)
    auth._store({"access_token": "abc", "refresh_token": "r", "expires_in": 3600})

    assert path.stat().st_mode & 0o077 == 0


def test_a_refresh_does_not_throw_away_the_refresh_token(tmp_path):
    """Google only sends the refresh token once, on the first grant.

    A refresh response has no `refresh_token` in it. Overwriting the stored one
    with that absence signs the user out on the *next* run, which is the sort
    of bug that takes a week to notice and cannot be undone.
    """
    auth = innertube.Auth(tmp_path / "auth.json")
    auth._store({"access_token": "first", "refresh_token": "keep-me", "expires_in": 3600})
    auth._store({"access_token": "second", "expires_in": 3600})

    assert auth.tokens.refresh_token == "keep-me"
    assert auth.tokens.access_token == "second"


def test_an_expired_token_is_not_used(tmp_path):
    auth = innertube.Auth(tmp_path / "auth.json")
    auth.tokens = innertube.Tokens(access_token="old", expires_at=time.time() - 1)
    assert not auth.tokens.usable

    auth.tokens = innertube.Tokens(access_token="new", expires_at=time.time() + 3600)
    assert auth.tokens.usable


def test_signing_out_takes_the_file_with_it(tmp_path):
    path = tmp_path / "auth.json"
    auth = innertube.Auth(path)
    auth._store({"access_token": "abc", "refresh_token": "r", "expires_in": 3600})

    auth.sign_out()
    assert not path.exists()
    assert not auth.signed_in


def test_a_corrupt_token_file_is_not_a_crash_on_startup(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{ this is not json")
    assert not innertube.Auth(path).signed_in


def test_a_refusal_to_refresh_signs_out_rather_than_failing_forever(tmp_path, monkeypatch):
    auth = innertube.Auth(tmp_path / "auth.json")
    auth._store({"access_token": "old", "refresh_token": "revoked", "expires_in": -10})

    monkeypatch.setattr(innertube, "_post_form",
                        lambda url, fields: (400, {"error": "invalid_grant"}))

    assert auth.access_token() == ""
    assert not auth.signed_in


def test_signing_in_reads_googles_answer(tmp_path, monkeypatch):
    auth = innertube.Auth(tmp_path / "auth.json")
    monkeypatch.setattr(innertube, "_post_form", lambda url, fields: (200, {
        "device_code": "DEV", "user_code": "ABC-DEF", "interval": 5,
        "expires_in": 1800, "verification_url": "https://www.google.com/device",
    }))

    code = auth.start()
    assert code.user_code == "ABC-DEF"
    assert code.interval == 5


@pytest.mark.parametrize("answer, waiting", [
    ({"error": "authorization_pending"}, True),
    ({"error": "slow_down"}, True),
])
def test_waiting_for_the_code_is_not_an_error(tmp_path, monkeypatch, answer, waiting):
    auth = innertube.Auth(tmp_path / "auth.json")
    monkeypatch.setattr(innertube, "_post_form", lambda url, fields: (428, answer))
    assert auth.poll(innertube.DeviceCode(device_code="DEV")) is False


def test_a_refused_sign_in_says_so(tmp_path, monkeypatch):
    auth = innertube.Auth(tmp_path / "auth.json")
    monkeypatch.setattr(innertube, "_post_form",
                        lambda url, fields: (403, {"error": "access_denied"}))

    with pytest.raises(innertube.AuthError):
        auth.poll(innertube.DeviceCode(device_code="DEV"))


def test_entering_the_code_signs_you_in(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    auth = innertube.Auth(path)
    monkeypatch.setattr(innertube, "_post_form", lambda url, fields: (200, {
        "access_token": "token", "refresh_token": "refresh", "expires_in": 3600}))

    assert auth.poll(innertube.DeviceCode(device_code="DEV")) is True
    assert auth.signed_in
    assert json.loads(path.read_text())["refresh_token"] == "refresh"


# ── What it will and will not ask for ─────────────────────────────

def test_a_signed_out_feed_does_not_go_to_the_network(tmp_path):
    """YouTube has no anonymous feeds, so asking is a round trip for nothing."""
    tube = innertube.InnerTube(innertube.Auth(tmp_path / "auth.json"))

    def refuse(*_args, **_kwargs):
        raise AssertionError("a request was made when signed out")

    tube._request = refuse

    page = tube.feed(innertube.HOME)
    assert page.items == []
    assert "Sign in" in page.note


# ── Signing in twice in one run ───────────────────────────────────

def test_being_told_to_slow_down_widens_the_interval(tmp_path, monkeypatch):
    """Google's answer to polling too fast is "wait longer", not "you failed".

    The interval is written back onto the code because the caller's timer reads
    it from there; without that the next poll is just as early, and the flow
    spends its whole life being told off instead of signing in.
    """
    auth = innertube.Auth(tmp_path / "auth.json")
    monkeypatch.setattr(innertube, "_post_form",
                        lambda url, fields: (428, {"error": "slow_down"}))

    code = innertube.DeviceCode(device_code="DEV", interval=5)
    assert auth.poll(code) is False
    assert code.interval == 10
    assert auth.poll(code) is False
    assert code.interval == 15


def test_a_path_may_be_a_string(tmp_path):
    """A str is the obvious thing to pass, so it should not fail three frames on."""
    auth = innertube.Auth(str(tmp_path / "auth.json"))
    auth.tokens = innertube.Tokens(access_token="a", refresh_token="b")
    auth._save()
    assert innertube.Auth(str(tmp_path / "auth.json")).signed_in
