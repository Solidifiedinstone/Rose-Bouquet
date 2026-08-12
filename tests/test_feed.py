"""Subscriptions, likes, and the recommendation algorithm.

All of it is pure functions over a local profile, so the algorithm is testable
without a network, an account, or anybody else's data.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from rose_bouquet.core.recommend import (
    Candidate,
    affinity,
    rank,
    score,
    seeds,
    top_artists,
)
from rose_bouquet.core.tastes import Channel, Signal, Tastes, decay
from rose_bouquet.core.youtube import channel_id

NOW = datetime(2026, 8, 12, 12, 0)


def at(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


@pytest.fixture
def tastes() -> Tastes:
    return Tastes()


def candidate(**kwargs) -> Candidate:
    base = {"id": "v1", "title": "A song", "artist": "Boards of Canada",
            "channel_id": "UCboc", "kind": "song"}
    base.update(kwargs)
    return Candidate(**base)


# ── Subscriptions ─────────────────────────────────────────────────

def test_subscribing_and_unsubscribing(tastes):
    channel = Channel(id="UCboc", title="Boards of Canada")

    assert tastes.toggle_subscription(channel) is True
    assert tastes.subscribed("UCboc")

    assert tastes.toggle_subscription(channel) is False
    assert not tastes.subscribed("UCboc")


def test_subscribing_twice_does_not_duplicate(tastes):
    tastes.subscribe(Channel(id="UCboc", title="Boards of Canada"))
    tastes.subscribe(Channel(id="UCboc", title="Boards of Canada"))
    assert len(tastes.subscriptions()) == 1


def test_a_channel_link_yields_its_id():
    assert channel_id("https://www.youtube.com/channel/UCabc123") == "UCabc123"
    assert channel_id("https://www.youtube.com/@warprecords") == "warprecords"
    assert channel_id("nonsense") == ""


# ── Likes ─────────────────────────────────────────────────────────

def test_liking_is_a_toggle(tastes):
    assert tastes.like("v1", "Roygbiv", "Boards of Canada") is True
    assert tastes.likes("v1")
    assert tastes.like("v1") is False
    assert not tastes.likes("v1")


def test_disliking_removes_a_like(tastes):
    tastes.like("v1", "Roygbiv", "Boards of Canada")
    tastes.dislike("v1", "Roygbiv", "Boards of Canada")

    assert not tastes.likes("v1")
    assert tastes.dislikes("v1")


def test_a_short_play_is_recorded_as_a_skip(tastes):
    """A skip is one of the strongest signals there is, so it is kept."""
    tastes.note_play("v1", completion=0.05)
    assert tastes.signals[0].kind == "skip"

    tastes.note_play("v2", completion=0.9)
    assert tastes.signals[1].kind == "play"


def test_history_is_capped_from_the_oldest_end(tastes):
    tastes.limit = 10
    for index in range(20):
        tastes.note_play(f"v{index}")

    assert len(tastes.signals) == 10
    assert tastes.signals[0].id == "v10"


def test_forgetting_an_item_removes_every_trace(tastes):
    tastes.like("v1", "Roygbiv")
    tastes.note_play("v1")
    tastes.forget("v1")
    assert not tastes.signals


def test_clearing_history_keeps_likes_and_subscriptions(tastes):
    tastes.subscribe(Channel(id="UCboc", title="BoC"))
    tastes.like("v1", "Roygbiv")
    tastes.note_play("v2")

    tastes.clear_history()

    assert tastes.likes("v1")
    assert tastes.subscribed("UCboc")
    assert not any(s.kind == "play" for s in tastes.signals)


def test_clearing_everything_leaves_no_opinion(tastes):
    tastes.subscribe(Channel(id="UCboc", title="BoC"))
    tastes.like("v1")
    tastes.clear_all()

    assert not tastes.signals
    assert not tastes.channels
    assert affinity(tastes) == {}


def test_tastes_round_trip(tmp_path):
    path = tmp_path / "tastes.json"
    tastes = Tastes()
    tastes.subscribe(Channel(id="UCboc", title="Boards of Canada"))
    tastes.like("v1", "Roygbiv", "Boards of Canada")
    tastes.save(path)

    restored = Tastes.load(path)
    assert restored.subscribed("UCboc")
    assert restored.likes("v1")


def test_a_corrupt_profile_loads_empty(tmp_path):
    path = tmp_path / "tastes.json"
    path.write_text("{ not json")
    assert Tastes.load(path).signals == []


# ── Decay ─────────────────────────────────────────────────────────

def test_a_signal_halves_in_weight_over_a_half_life():
    assert decay(at(120), NOW) == pytest.approx(0.5, abs=0.01)
    assert decay(at(0), NOW) == pytest.approx(1.0, abs=0.01)


def test_an_unparseable_date_counts_as_now():
    assert decay("not a date", NOW) == pytest.approx(1.0)


# ── Affinity ──────────────────────────────────────────────────────

def test_plays_build_affinity_for_an_artist(tastes):
    for _ in range(3):
        tastes.record(Signal(id="v1", artist="Aphex Twin", kind="play", at=at(1)))

    scores = affinity(tastes, NOW)
    assert scores["aphex twin"] > 0


def test_skips_count_against_an_artist(tastes):
    tastes.record(Signal(id="v1", artist="Liked", kind="play", at=at(1)))
    tastes.record(Signal(id="v2", artist="Skipped", kind="skip", at=at(1)))

    scores = affinity(tastes, NOW)
    assert scores["liked"] > 0
    assert scores["skipped"] < 0


def test_recent_listening_outweighs_old_listening(tastes):
    tastes.record(Signal(id="v1", artist="Recent", kind="play", at=at(1)))
    tastes.record(Signal(id="v2", artist="Ancient", kind="play", at=at(700)))

    scores = affinity(tastes, NOW)
    assert scores["recent"] > scores["ancient"]


def test_subscribing_counts_as_affinity(tastes):
    tastes.subscribe(Channel(id="UCboc", title="Boards of Canada", subscribed_at=at(1)))
    scores = affinity(tastes, NOW)
    assert scores["UCboc"] > 0


def test_a_muted_channel_contributes_nothing(tastes):
    tastes.subscribe(Channel(id="UCboc", title="BoC", subscribed_at=at(1), muted=True))
    assert affinity(tastes, NOW).get("UCboc", 0) == 0


def test_top_artists_are_ordered_by_strength(tastes):
    for _ in range(5):
        tastes.record(Signal(id="a", artist="Favourite", kind="play", at=at(1)))
    tastes.record(Signal(id="b", artist="Occasional", kind="play", at=at(1)))

    assert top_artists(tastes, now=NOW)[0][0] == "favourite"


# ── Scoring ───────────────────────────────────────────────────────

def test_something_you_follow_scores_above_something_you_do_not(tastes):
    tastes.subscribe(Channel(id="UCboc", title="BoC", subscribed_at=at(1)))

    followed = score(candidate(id="v1", channel_id="UCboc"), tastes, now=NOW)
    stranger = score(candidate(id="v2", channel_id="UCother", artist="Someone"), tastes, now=NOW)

    assert followed.score > stranger.score


def test_a_liked_artist_lifts_their_other_work(tastes):
    tastes.like("v0", "Roygbiv", "Boards of Canada")

    same = score(candidate(id="v1"), tastes, now=NOW)
    other = score(candidate(id="v2", artist="Nobody", channel_id="UCz"), tastes, now=NOW)

    assert "liked" in same.terms
    assert same.score > other.score


def test_novelty_favours_what_you_have_not_heard(tastes):
    for _ in range(10):
        tastes.record(Signal(id="heard", artist="A", kind="play", at=at(1)))

    known = score(candidate(id="heard", artist="A"), tastes, now=NOW)
    fresh = score(candidate(id="new", artist="A"), tastes, now=NOW)

    assert fresh.terms["novelty"] > known.terms["novelty"]


def test_a_recent_upload_scores_above_an_old_one(tastes):
    new = score(candidate(id="v1", published=at(1)[:10]), tastes, now=NOW)
    old = score(candidate(id="v2", published=at(400)[:10]), tastes, now=NOW)

    assert new.terms.get("freshness", 0) > old.terms.get("freshness", 0)


def test_every_score_explains_itself(tastes):
    tastes.subscribe(Channel(id="UCboc", title="Boards of Canada", subscribed_at=at(1)))
    scored = score(candidate(), tastes, now=NOW)

    assert scored.terms
    assert scored.why


def test_weights_can_be_overridden(tastes):
    tastes.subscribe(Channel(id="UCboc", title="BoC", subscribed_at=at(1)))

    normal = score(candidate(), tastes, now=NOW)
    without = score(candidate(), tastes, weights={"following": 0.0}, now=NOW)

    assert without.score < normal.score


# ── Ranking ───────────────────────────────────────────────────────

def test_disliked_items_are_dropped_not_demoted(tastes):
    tastes.dislike("bad", "Nope")
    ranked = rank([candidate(id="bad"), candidate(id="good")], tastes, now=NOW)

    assert [s.candidate.id for s in ranked] == ["good"]


def test_duplicates_are_collapsed(tastes):
    ranked = rank([candidate(id="v1"), candidate(id="v1")], tastes, now=NOW)
    assert len(ranked) == 1


def test_the_feed_keeps_room_for_the_unfamiliar(tastes):
    """A recommender that only confirms what it knows stops being useful."""
    tastes.subscribe(Channel(id="UCknown", title="Known", subscribed_at=at(1)))

    familiar = [candidate(id=f"k{i}", channel_id="UCknown") for i in range(40)]
    strangers = [
        candidate(id=f"s{i}", channel_id=f"UCs{i}", artist=f"Stranger {i}")
        for i in range(40)
    ]

    ranked = rank(familiar + strangers, tastes, limit=20, explore=0.25, now=NOW)
    unfamiliar = [s for s in ranked if s.candidate.id.startswith("s")]

    assert len(unfamiliar) >= 4
    assert len(ranked) == 20


def test_ranking_is_ordered_by_score(tastes):
    tastes.subscribe(Channel(id="UCboc", title="BoC", subscribed_at=at(1)))
    ranked = rank(
        [candidate(id="stranger", channel_id="UCx", artist="Someone"),
         candidate(id="followed", channel_id="UCboc")],
        tastes, now=NOW,
    )
    assert ranked[0].candidate.id == "followed"


def test_an_empty_profile_still_produces_a_feed(tastes):
    """A brand-new install has no history and must not show an empty page."""
    ranked = rank([candidate(id=f"v{i}", artist=f"A{i}") for i in range(10)],
                  tastes, limit=5, now=NOW)
    assert len(ranked) == 5


def test_seeds_are_taken_from_likes_and_repeats(tastes):
    tastes.like("liked", "A liked song", "Artist")
    for _ in range(4):
        tastes.record(Signal(id="repeated", title="On repeat", kind="play", at=at(2)))
    tastes.record(Signal(id="once", title="Heard once", kind="play", at=at(2)))

    chosen = [s.id for s in seeds(tastes)]
    assert "liked" in chosen
    assert "repeated" in chosen
    assert "once" not in chosen
