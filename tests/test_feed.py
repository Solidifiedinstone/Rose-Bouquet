"""Subscriptions, likes, and the recommendation algorithm.

All of it is pure functions over a local profile, so the algorithm is testable
without a network, an account, or anybody else's data.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from rose_bouquet.core.recommend import (
    Candidate,
    Scored,
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
    """A candidate for tests, with a title unique to its id.

    Unique on purpose: the ranker drops near-duplicate titles, so a fixture
    that gives forty candidates the same title is testing that de-duplication
    rather than whatever it meant to test.
    """
    base = {"id": "v1", "artist": "Boards of Canada",
            "channel_id": "UCboc", "kind": "song"}
    base.update(kwargs)
    # The unique part has to be a word: short tokens are dropped when a
    # title is reduced to its meaningful words, so "v0" would not distinguish
    # anything and every fixture would collapse into one.
    base.setdefault("title", f"A song called subject{base['id']}")
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


def test_seeds_prefer_likes_and_repeats(tastes):
    """Likes and replays still win — they are just no longer the entry fee."""
    tastes.like("liked", "A liked song", "Artist")
    for _ in range(4):
        tastes.record(Signal(id="repeated", title="On repeat", kind="play", at=at(2)))
    tastes.record(Signal(id="once", title="Heard once", kind="play", at=at(2)))

    chosen = [s.id for s in seeds(tastes)]
    assert chosen.index("liked") < chosen.index("once")
    assert chosen.index("repeated") < chosen.index("once")


def test_a_history_of_single_watches_still_seeds_a_feed(tastes):
    """An imported Takeout history is *only* single watches, with no likes.

    Requiring a like or a third play made a fresh import produce no seeds at
    all, so the feed never grew past subscription uploads.
    """
    for index in range(5):
        tastes.record(Signal(id=f"v{index}", title=f"Video {index}",
                             artist=f"Channel {index}", kind="play", at=at(1)))

    assert len(seeds(tastes, limit=4)) == 4


def test_seeds_do_not_all_come_from_one_channel(tastes):
    """Twenty videos from one creator must not claim every seed, or every
    'related' call comes back with more of the same creator."""
    for index in range(20):
        tastes.record(Signal(id=f"same{index}", title=f"Ep {index}",
                             artist="One Creator", channel_id="UCone",
                             kind="play", at=at(1)))
    tastes.record(Signal(id="other", title="Something else", artist="Someone",
                         channel_id="UCtwo", kind="play", at=at(3)))

    chosen = [s.id for s in seeds(tastes, limit=4)]
    assert "other" in chosen
    assert sum(1 for c in chosen if c.startswith("same")) == 1


def test_one_channel_cannot_own_the_top_of_the_feed(tastes):
    """A prolific channel you watch a lot outscores everything. Capping its
    total is not enough — uncapped ordering still hands it the first N slots."""
    tastes.record(Signal(id="seen", artist="Prolific", channel_id="UCbig",
                         kind="play", at=at(1)))

    candidates = [
        Candidate(id=f"big{i}", title=f"Bigchannel upload number {i} unique{i}", artist="Prolific",
                  channel_id="UCbig", published=at(1))
        for i in range(20)
    ]
    candidates += [
        Candidate(id=f"small{i}", title=f"Smallchannel piece {i} unique{i}", artist=f"Other {i}",
                  channel_id=f"UCsmall{i}", published=at(1))
        for i in range(6)
    ]

    ranked = rank(candidates, tastes, limit=10)
    top = [r.candidate.channel_id for r in ranked[:5]]

    assert top.count("UCbig") <= 1, f"one channel took the top: {top}"
    assert len(set(top)) >= 4


def test_spreading_never_shortens_a_feed(tastes):
    """Variety reorders; it must not throw items away."""
    candidates = [
        Candidate(id=f"v{i}", title=f"Distinct subject {i} unique{i}", artist="One", channel_id="UCone")
        for i in range(30)
    ]
    ranked = rank(candidates, tastes, limit=20)
    assert len(ranked) == 20


def test_a_feed_survives_a_restart(tmp_path):
    """Held only in memory, "For you" is empty on every launch."""
    from rose_bouquet.core.recommend import load_feed, save_feed

    path = tmp_path / "feed.json"
    items = [
        Scored(Candidate(id="a", title="First", artist="One", channel_id="UC1"),
               1.5, {"affinity": 1.5}),
        Scored(Candidate(id="b", title="Second", artist="Two"), 0.6, {"novelty": 0.6}),
    ]
    save_feed(items, path)
    restored, built_at = load_feed(path)

    assert [s.candidate.id for s in restored] == ["a", "b"]
    assert restored[0].why == "You listen to One"
    assert built_at


def test_no_saved_feed_is_not_an_error(tmp_path):
    from rose_bouquet.core.recommend import load_feed

    assert load_feed(tmp_path / "nothing.json") == ([], "")


def test_a_feed_from_an_older_version_still_loads(tmp_path):
    """Fields that no longer exist must not take the whole feed down."""
    import json

    from rose_bouquet.core.recommend import load_feed

    path = tmp_path / "feed.json"
    path.write_text(json.dumps({
        "at": "2026-01-01T00:00:00",
        "items": [{"candidate": {"id": "a", "title": "T", "retired_field": 1},
                   "score": 1.0, "terms": {}}],
    }))

    restored, _ = load_feed(path)
    assert len(restored) == 1
    assert restored[0].candidate.title == "T"


def test_watch_time_counts_for_more_than_a_click(tastes):
    """Two plays, same age; the one actually watched through should win."""
    tastes.record(Signal(id="finished", title="Watched", artist="A",
                         kind="play", completion=1.0, at=at(1)))
    tastes.record(Signal(id="bailed", title="Bailed on", artist="B",
                         kind="play", completion=0.3, at=at(1)))

    strength = affinity(tastes)
    assert strength["a"] > strength["b"]


# ── Resumable imports ─────────────────────────────────────────────

from rose_bouquet.core import imports  # noqa: E402


class FakeTrack:
    def __init__(self, title, artist=""):
        self.title = title
        self.artist = artist


def test_an_import_records_every_track_once():
    job = imports.ImportJob(title="Mix")
    tracks = [FakeTrack("A", "One"), FakeTrack("B", "Two")]

    assert job.add_tracks(tracks) == 2
    # Importing the same playlist again must not duplicate rows.
    assert job.add_tracks(tracks) == 0
    assert job.total == 2


def test_a_part_read_playlist_tops_up_the_same_record():
    """Read 100, come back for the rest — the point of the whole file."""
    job = imports.ImportJob(title="Long")
    job.add_tracks([FakeTrack(f"Song {i}") for i in range(100)])
    job.partial = True

    added = job.add_tracks([FakeTrack(f"Song {i}") for i in range(250)])

    assert added == 150
    assert job.total == 250


def test_pending_is_what_is_matched_but_not_downloaded():
    job = imports.ImportJob(title="Mix")
    job.add_tracks([FakeTrack("A"), FakeTrack("B"), FakeTrack("C")])

    job.note_match(job.entries[0], "vid-a")
    job.note_match(job.entries[1], "")            # nothing found
    job.note_match(job.entries[2], "vid-c")
    job.note_done("vid-c", "/music/c.mp3")

    assert [e.title for e in job.pending()] == ["A"]
    assert [e.title for e in job.missing()] == ["B"]
    assert job.count(imports.DONE) == 1


def test_a_failed_download_is_recorded_and_not_lost():
    job = imports.ImportJob(title="Mix")
    job.add_tracks([FakeTrack("A")])
    job.note_match(job.entries[0], "vid-a")

    job.note_failed("vid-a", "YouTube said no")

    assert job.count(imports.FAILED) == 1
    assert job.entries[0].error == "YouTube said no"


def test_tracks_already_in_the_library_are_not_downloaded_again():
    from rose_bouquet.core.library import Library, Track

    library = Library()
    library.add(Track(path="/music/a.mp3", title="A", artist="One", source_id="vid-a"))
    library.add(Track(path="/music/b.mp3", title="B", artist="Two"))

    job = imports.ImportJob(title="Mix")
    job.add_tracks([FakeTrack("A", "One"), FakeTrack("B", "Two"), FakeTrack("C", "Three")])
    job.note_match(job.entries[0], "vid-a")

    skipped = job.skip_already_downloaded(library)

    assert skipped == 2                      # by video id, and by artist+title
    assert job.count(imports.DONE) == 2
    assert job.entries[2].state == imports.PENDING


def test_an_import_round_trips_through_disk(tmp_path):
    job = imports.ImportJob(title="Mix", link="https://open.spotify.com/playlist/abc")
    job.add_tracks([FakeTrack("A", "One")])
    job.note_match(job.entries[0], "vid-a")
    path = job.save(tmp_path)

    restored = imports.ImportJob.load(path)
    assert restored.title == "Mix"
    assert restored.entries[0].video_id == "vid-a"
    assert restored.entries[0].state == imports.MATCHED


def test_importing_the_same_playlist_continues_it(tmp_path):
    first = imports.ImportJob(title="Mix", link="https://open.spotify.com/playlist/abc")
    first.add_tracks([FakeTrack("A")])
    first.save(tmp_path)

    again = imports.ImportJob.for_link(
        "https://open.spotify.com/playlist/abc", "Mix", tmp_path)

    assert again.total == 1                  # the existing record, not a new one


def test_unfinished_lists_only_imports_with_work_left(tmp_path):
    done = imports.ImportJob(title="Finished")
    done.add_tracks([FakeTrack("A")])
    done.entries[0].state = imports.DONE
    done.save(tmp_path)

    todo = imports.ImportJob(title="Halfway")
    todo.add_tracks([FakeTrack("B")])
    todo.note_match(todo.entries[0], "vid-b")
    todo.save(tmp_path)

    assert [j.title for j in imports.unfinished(tmp_path)] == ["Halfway"]


def test_a_finished_import_says_so():
    job = imports.ImportJob(title="Mix")
    job.add_tracks([FakeTrack("A"), FakeTrack("B")])
    job.note_match(job.entries[0], "vid-a")
    job.note_done("vid-a", "/music/a.mp3")
    job.entries[1].state = imports.MISSING

    assert job.finished
    assert "1 of 2 downloaded" in job.summary


# ── Takeout ───────────────────────────────────────────────────────

from rose_bouquet.core import takeout  # noqa: E402


def test_watch_history_json_is_read():
    text = """[
      {"header": "YouTube", "title": "Watched Roygbiv",
       "titleUrl": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
       "subtitles": [{"name": "Boards of Canada",
                      "url": "https://www.youtube.com/channel/UCbocbocbocbocbocbocboc"}],
       "time": "2026-01-15T20:11:00.000Z"},
      {"header": "YouTube", "title": "Watched a video that has been removed"}
    ]"""
    watched = takeout.parse_watch_history_json(text)

    assert len(watched) == 1                 # the removed one has no id
    assert watched[0].video_id == "aaaaaaaaaaa"
    assert watched[0].title == "Roygbiv"
    assert watched[0].channel == "Boards of Canada"
    assert watched[0].channel_id.startswith("UC")


def test_the_html_export_is_read_too():
    """Because 'export it again, differently' is a miserable thing to be told."""
    text = '''<div class="content-cell mdl-cell">
        <a href="https://www.youtube.com/watch?v=bbbbbbbbbbb">Xtal</a><br>
        <a href="https://www.youtube.com/channel/UCaphexaphexaphexaphex">Aphex Twin</a><br>
        Jan 15, 2026</div>'''
    watched = takeout.parse_watch_history_html(text)

    assert len(watched) == 1
    assert watched[0].video_id == "bbbbbbbbbbb"
    assert watched[0].channel == "Aphex Twin"


def test_the_html_export_keeps_its_watch_dates():
    """Real exports write "Aug 12, 2026, 5:16:39 PM PDT", with a narrow
    no-break space before the AM/PM. Miss it and a decade of history is all
    stamped with the moment of import."""
    text = (
        '<div class="content-cell mdl-cell">Watched\xa0'
        '<a href="https://www.youtube.com/watch?v=bbbbbbbbbbb">Xtal</a><br>'
        '<a href="https://www.youtube.com/channel/UCaphexaphexaphexaphex">Aphex Twin</a><br>'
        'Aug 12, 2026, 5:16:39 PM PDT<br></div>'
    )
    watched = takeout.parse_watch_history_html(text)

    assert len(watched) == 1
    assert watched[0].at == "2026-08-12T17:16:39"


def test_an_html_entry_with_no_date_is_still_imported():
    """A missing date is not a reason to drop the video."""
    text = ('<div class="content-cell mdl-cell">'
            '<a href="https://www.youtube.com/watch?v=ccccccccccc">Untitled</a></div>')
    watched = takeout.parse_watch_history_html(text)

    assert len(watched) == 1
    assert watched[0].at == ""


def test_reimporting_the_same_export_adds_nothing():
    """The second import of a good export is a no-op, not a failure."""
    data = takeout.TakeoutData(
        watched=[takeout.Watched(video_id="v1", title="A", channel="Warp")],
        subscriptions=[takeout.Subscription(channel_id="UCwarp", title="Warp")],
    )
    tastes = Tastes()

    assert takeout.apply(data, tastes) == (1, 1)
    assert takeout.apply(data, tastes) == (0, 0)
    # The distinction the interface leans on: nothing new, but plenty usable.
    assert data.watched and data.subscriptions


def test_subscriptions_csv_is_read():
    text = ("Channel Id,Channel Url,Channel Title\n"
            "UCwarpwarpwarpwarpwarpwa,https://www.youtube.com/channel/UCwarpwarpwarpwarpwarpwa,Warp Records\n")
    subscriptions = takeout.parse_subscriptions_csv(text)

    assert len(subscriptions) == 1
    assert subscriptions[0].title == "Warp Records"


def test_importing_takeout_builds_a_profile():
    data = takeout.TakeoutData(
        watched=[takeout.Watched(video_id="v1", title="A", channel="Warp",
                                 channel_id="UCwarp", at="2026-01-15T20:11:00.000Z")],
        subscriptions=[takeout.Subscription(channel_id="UCwarp", title="Warp Records")],
    )
    tastes = Tastes()
    plays, followed = takeout.apply(data, tastes)

    assert (plays, followed) == (1, 1)
    assert tastes.subscribed("UCwarp")
    # The original timestamp is kept, so old history is weighted as old.
    assert tastes.signals[0].at.startswith("2026-01-15")


def test_importing_the_same_export_twice_changes_nothing():
    data = takeout.TakeoutData(
        watched=[takeout.Watched(video_id="v1", title="A")],
        subscriptions=[takeout.Subscription(channel_id="UCwarp", title="Warp")],
    )
    tastes = Tastes()
    takeout.apply(data, tastes)
    plays, followed = takeout.apply(data, tastes)

    assert (plays, followed) == (0, 0)
    assert len(tastes.signals) == 1


def test_a_takeout_zip_is_read(tmp_path):
    import zipfile

    archive = tmp_path / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Takeout/YouTube/history/watch-history.json",
                    '[{"title": "Watched X", "titleUrl": "https://www.youtube.com/watch?v=ccccccccccc"}]')
        zf.writestr("Takeout/YouTube/subscriptions/subscriptions.csv",
                    "Channel Id,Channel Url,Channel Title\nUCabc,http://x,Some Channel\n")

    data = takeout.read(archive)
    assert len(data.watched) == 1
    assert len(data.subscriptions) == 1
    assert "1 watched videos and 1 subscriptions" in data.summary


def test_imported_history_actually_shapes_the_feed():
    """The whole point: after an import, the ranker knows what you like."""
    from rose_bouquet.core.recommend import Candidate, rank

    data = takeout.TakeoutData(watched=[
        takeout.Watched(video_id=f"v{i}", title=f"Song {i}", channel="Warp Records",
                        channel_id="UCwarp", at=at(3))
        for i in range(20)
    ])
    tastes = Tastes()
    takeout.apply(data, tastes)

    ranked = rank([Candidate(id="new", title="A Warp upload", channel_id="UCwarp",
                             artist="Warp Records"),
                   Candidate(id="other", title="Unrelated", artist="Nobody")],
                  tastes, limit=2, now=NOW)

    assert ranked[0].candidate.id == "new"
    assert "affinity" in ranked[0].terms


# ── Streaming ─────────────────────────────────────────────────────

def test_streaming_asks_the_clients_that_actually_serve_first():
    """Downloading and streaming are different problems.

    yt-dlp fetches downloads itself, so any client's URL works. Streaming hands
    the bare URL to Qt, and several clients now return URLs that answer 403 to
    anyone else — so the order here is not the downloader's order.
    """
    from rose_bouquet.core.youtube import (
        FALLBACK_STREAM_CLIENTS,
        PLAYER_CLIENTS,
        STREAM_CLIENTS,
    )

    # Asked in tiers, not all at once: yt-dlp queries every client it is given
    # even after one has already worked, which measured 4.4s against 0.96s.
    assert STREAM_CLIENTS == ["android"]
    assert "tv" not in STREAM_CLIENTS
    assert "web" not in STREAM_CLIENTS
    assert STREAM_CLIENTS != PLAYER_CLIENTS

    # The refusing clients are still reachable, just not asked first.
    assert set(FALLBACK_STREAM_CLIENTS) & {"tv", "web"}
    assert not set(STREAM_CLIENTS) & set(FALLBACK_STREAM_CLIENTS)


def test_a_local_path_needs_no_probing():
    from rose_bouquet.core.youtube import playable

    assert playable("/music/song.mp3")
    assert not playable("")


def test_a_refused_url_is_not_playable(monkeypatch):
    """A 403 must be caught here, not surfaced as 'could not open file'."""
    import urllib.error

    from rose_bouquet.core import youtube

    def refuse(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    assert not youtube.playable("https://example.com/stream")


def test_a_network_wobble_does_not_condemn_a_url(monkeypatch):
    """A probe that cannot complete says nothing about the URL, so play anyway."""
    from rose_bouquet.core import youtube

    def blow_up(*_args, **_kwargs):
        raise TimeoutError("no route")

    monkeypatch.setattr("urllib.request.urlopen", blow_up)
    assert youtube.playable("https://example.com/stream")


def test_channels_are_read_in_parallel_but_not_unboundedly():
    """Forty channels one at a time is twenty seconds of waiting; forty at
    once is a home connection and YouTube's patience both abused."""
    from rose_bouquet.core.youtube import CHANNEL_WORKERS

    assert 2 <= CHANNEL_WORKERS <= 16


def test_a_shorts_search_asks_youtube_for_shorts():
    """Filtering an ordinary search by duration finds almost nothing: a search
    for 'skateboard' returns full-length videos and correctly discards every
    one, leaving an empty screen."""
    import inspect

    from rose_bouquet.core.youtube import YouTube

    assert "#shorts" in inspect.getsource(YouTube.search)


def test_a_stream_url_is_not_probed_forever():
    """A video can offer thirty formats; testing them all at seconds apiece is
    minutes of waiting to learn what the first two already said."""
    from rose_bouquet.core.youtube import PROBE_LIMIT, PROBE_TIMEOUT

    assert 1 <= PROBE_LIMIT <= 5
    assert PROBE_TIMEOUT <= 3.0


def test_the_stream_cache_answers_without_the_network(monkeypatch):
    from rose_bouquet.core.youtube import StreamCache

    calls = []

    class FakeYouTube:
        def stream_url(self, video_id, *, audio_only=True):
            calls.append(video_id)
            return f"https://example/{video_id}"

    cache = StreamCache(FakeYouTube())
    try:
        assert cache.cached("abc") == ""            # nothing yet, no network
        assert cache.resolve("abc") == "https://example/abc"
        assert cache.cached("abc") == "https://example/abc"
        assert cache.resolve("abc") == "https://example/abc"
        assert calls == ["abc"], "a cached url must not be fetched twice"

        cache.forget("abc")
        assert cache.cached("abc") == ""
    finally:
        cache.close()


def test_a_closed_cache_does_not_start_new_work():
    from rose_bouquet.core.youtube import StreamCache

    class FakeYouTube:
        def stream_url(self, video_id, *, audio_only=True):
            raise AssertionError("should not be called after close")

    cache = StreamCache(FakeYouTube())
    cache.close()
    cache.prefetch(["abc"])          # must be a no-op, not a crash


# ── Interests and discovery ───────────────────────────────────────

def test_a_blocked_topic_is_removed_not_merely_ranked_down(tastes):
    """"I never want this" that quietly means "less of this" is the thing
    everybody hates about recommendation feeds."""
    from rose_bouquet.core.interests import Interests

    tastes.interests = Interests(blocked=["politics"])
    kept = rank([
        Candidate(id="a", title="Politics Explained", artist="News"),
        Candidate(id="b", title="A Guitar Riff", artist="Someone"),
    ], tastes, limit=10)

    assert [s.candidate.id for s in kept] == ["b"]


def test_blocking_matches_whole_words_only(tastes):
    """Blocking "war" must not also hide "warm", "software" and "Warsaw"."""
    from rose_bouquet.core.interests import Interests

    interests = Interests(blocked=["war"])
    assert interests.blocks(title="The War Years")
    assert not interests.blocks(title="Warm Sweaters")
    assert not interests.blocks(title="Software Review")


def test_a_stated_interest_outranks_a_guess(tastes):
    from rose_bouquet.core.interests import Interests

    tastes.interests = Interests(wanted=["lego"])
    ranked = rank([
        Candidate(id="a", title="Something Ordinary", artist="A"),
        Candidate(id="b", title="A Lego Castle", artist="B"),
    ], tastes, limit=10)

    assert ranked[0].candidate.id == "b"
    assert ranked[0].why == "Matches an interest you set"


def test_a_blocked_channel_is_dropped_by_name(tastes):
    from rose_bouquet.core.interests import Interests

    tastes.interests = Interests(blocked_channels=["Annoying Channel"])
    kept = rank([
        Candidate(id="a", title="Fine", artist="Annoying Channel"),
        Candidate(id="b", title="Fine", artist="Someone Else"),
    ], tastes, limit=10)

    assert [s.candidate.id for s in kept] == ["b"]


def test_discovery_is_not_buried_under_every_subscription(tastes):
    """Anything from a followed channel outscores anything from an unknown
    one, so without a weight of its own a discovery lands below sixty
    familiar rows where nobody scrolls."""
    tastes.subscribe(Channel(id="UCknown", title="Known", subscribed_at=at(1)))

    candidates = [
        Candidate(id=f"k{i}", title=f"Known upload {i} unique{i}", artist="Known",
                  channel_id="UCknown")
        for i in range(20)
    ]
    candidates += [
        Candidate(id=f"n{i}", title=f"Stranger upload {i} unique{i}", artist=f"Stranger {i}",
                  channel_id=f"UCnew{i}", source="discover")
        for i in range(5)
    ]

    top = [s.candidate.source for s in rank(candidates, tastes, limit=10)[:5]]
    assert "discover" in top, top


def test_topics_come_out_of_what_was_actually_watched(tastes):
    from rose_bouquet.core.interests import derive_topics

    for _ in range(3):
        tastes.record(Signal(id="a", title="Zelda Ocarina of Time speedrun",
                             kind="play", at=at(1)))
    tastes.record(Signal(id="b", title="Unrelated cooking video",
                         kind="play", at=at(1)))

    topics = dict(derive_topics(tastes))
    assert topics.get("zelda", 0) > topics.get("cooking", 0)


def test_a_skipped_subject_counts_against_itself(tastes):
    """Bailing out is the strongest signal a short-form feed has."""
    from rose_bouquet.core.interests import derive_topics

    tastes.record(Signal(id="a", title="Gambling stream highlights",
                         kind="skip", at=at(1)))
    assert "gambling" not in dict(derive_topics(tastes))


def test_search_terms_prefer_what_was_asked_for(tastes):
    from rose_bouquet.core.interests import Interests, search_terms

    tastes.record(Signal(id="a", title="Zelda Ocarina", kind="play", at=at(1)))
    terms = search_terms(tastes, Interests(wanted=["guitar pedals"]), limit=4)
    assert terms[0] == "guitar pedals"


def test_the_same_thing_twice_is_shown_once():
    """A related-video lookup returns reuploads and mirrors of its seed.

    "Lets get right into the news" four times is not a feed.
    """
    from rose_bouquet.core.recommend import drop_near_duplicates

    items = [
        Candidate(id="a", title="Lets get right into the news"),
        Candidate(id="b", title="Let's get right into the news!"),
        Candidate(id="c", title="LETS GET RIGHT INTO THE NEWS"),
        Candidate(id="d", title="A completely different subject entirely"),
    ]
    kept = drop_near_duplicates(items)
    assert [c.id for c in kept] == ["a", "d"]


def test_slop_never_reaches_the_feed(tastes):
    """Not a morality filter: these are the two things a topic search drags in
    that nobody asked for — engagement bait and bulk-generated filler."""
    kept = rank([
        Candidate(id="a", title="AI Generated Music Compilation 10 Hours"),
        Candidate(id="b", title="You Wont Believe What Happened Next"),
        Candidate(id="c", title="Zelda Ocarina of Time speedrun explained"),
    ], tastes, limit=10)

    assert [s.candidate.id for s in kept] == ["c"]


def test_the_slop_filter_does_not_catch_ordinary_things():
    """A filter that hides cooking videos is a filter people turn off."""
    from rose_bouquet.core.interests import is_slop

    for innocent in ("Hot Sauce Recipe", "How to fix a hot water heater",
                     "Steam Deck review", "AI explained by a researcher"):
        assert not is_slop(innocent), innocent


def test_the_slop_filter_matches_on_word_boundaries():
    """Not on spaces, and not on bare substrings — both are wrong.

    Spaces miss "(AI COVER)", which is the commonest form of the commonest
    phrase in the list; bare substrings fire on "sora" inside "Sorabji" and
    quietly delete a piano recital, which nobody would ever trace back to here.
    """
    from rose_bouquet.core.interests import is_slop

    for punctuated in ("Bohemian Rhapsody (AI COVER)", "Hotel California [ai cover]",
                       "Something - AI cover!", "NSFW.", "Made with Sora"):
        assert is_slop(punctuated), punctuated

    for inside_a_longer_word in ("A history of the Sorabji piano sonatas",
                                 "Veolia water treatment works",
                                 "Building a 300 watts amplifier",
                                 "The Essex marshes at dawn"):
        assert not is_slop(inside_a_longer_word), inside_a_longer_word


def test_shorts_history_does_not_decide_the_video_feed(tastes):
    """What somebody flicks past at one in the morning is not what they sat
    down to watch."""
    from rose_bouquet.core.interests import derive_topics

    tastes.record(Signal(id="a", title="Skateboard trick compilation",
                         kind="play", form="short", at=at(1)))
    tastes.record(Signal(id="b", title="Zelda documentary retrospective",
                         kind="play", form="video", at=at(1)))

    video_topics = dict(derive_topics(tastes, forms=("video",)))
    assert "zelda" in video_topics
    assert "skateboard" not in video_topics


def test_seeds_can_be_drawn_from_one_kind_of_history(tastes):
    tastes.record(Signal(id="s1", title="A short thing", kind="play",
                         form="short", at=at(1)))
    tastes.record(Signal(id="v1", title="A long thing", kind="play",
                         form="video", at=at(1)))

    assert [s.id for s in seeds(tastes, forms=("video",))] == ["v1"]
    assert [s.id for s in seeds(tastes, forms=("short",))] == ["s1"]


def test_history_recorded_before_forms_existed_reads_as_video(tastes):
    """Everybody's existing profile has no form on it, and nearly all of it
    was ordinary videos."""
    from rose_bouquet.core.interests import derive_topics

    signal = Signal(id="a", title="An older watched thing", kind="play", at=at(1))
    signal.form = ""
    tastes.record(signal)

    assert "older" in dict(derive_topics(tastes, forms=("video",)))


def test_something_already_watched_is_not_recommended_again(tastes):
    """The novelty term ranks a seen video lower but still shows it, which is
    not what "recommend" means — and the feed is grown from things already
    watched, so without this it hands them straight back."""
    tastes.record(Signal(id="seen", title="A thing already watched", kind="play"))
    tastes.record(Signal(id="liked", title="A thing already liked", kind="like"))
    tastes.record(Signal(id="bailed", title="A thing bailed out of", kind="skip"))

    kept = rank([
        Candidate(id="seen", title="A thing already watched"),
        Candidate(id="liked", title="A thing already liked"),
        Candidate(id="bailed", title="A thing bailed out of"),
        Candidate(id="fresh", title="Something genuinely unseen"),
    ], tastes, limit=10)

    assert [s.candidate.id for s in kept] == ["fresh"]


def test_repeats_can_be_asked_for(tastes):
    """Ordering a library is not the same job as recommending from one."""
    tastes.record(Signal(id="seen", title="A thing already watched", kind="play"))

    kept = rank([Candidate(id="seen", title="A thing already watched")],
                tastes, limit=10, repeats=True)
    assert [s.candidate.id for s in kept] == ["seen"]
