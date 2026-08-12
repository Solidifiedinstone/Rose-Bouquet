"""The queue, the library, playlists, the cava bridge, Spotify import and the server."""

from __future__ import annotations

import json
import random

import pytest

from rose_bouquet.core import cava, spotify
from rose_bouquet.core.library import Library, Track, read_track
from rose_bouquet.core.playlists import Playlist, PlaylistStore, safe_name
from rose_bouquet.core.playqueue import Queue, Repeat
from rose_bouquet.core.server import ServerConfig, _track_id, check_token


def make_tracks(count: int) -> list[Track]:
    return [
        Track(path=f"/music/{i}.mp3", title=f"Track {i}", artist="Someone",
              album="An album", duration=180, track_number=i)
        for i in range(1, count + 1)
    ]


@pytest.fixture
def queue() -> Queue:
    q = Queue(rng=random.Random(7))       # seeded, so shuffle is reproducible
    q.set_tracks(make_tracks(5))
    return q


# ── The queue ─────────────────────────────────────────────────────

def test_a_new_queue_starts_at_the_first_track(queue):
    assert queue.current.title == "Track 1"
    assert len(queue.upcoming) == 4


def test_next_walks_the_queue(queue):
    assert queue.next().title == "Track 2"
    assert queue.next().title == "Track 3"


def test_the_queue_ends(queue):
    for _ in range(4):
        queue.next()
    assert queue.current.title == "Track 5"
    assert queue.next() is None


def test_repeat_all_wraps_around(queue):
    queue.repeat = Repeat.ALL
    for _ in range(4):
        queue.next()
    assert queue.next().title == "Track 1"


def test_repeat_one_replays_the_same_track(queue):
    queue.repeat = Repeat.ONE
    queue.next()
    assert queue.current.title == "Track 1"


def test_a_manual_skip_beats_repeat_one(queue):
    """Repeat-one must not break the skip button."""
    queue.repeat = Repeat.ONE
    assert queue.next(manual=True).title == "Track 2"


def test_previous_restarts_a_track_already_under_way(queue):
    queue.next()
    assert queue.previous(elapsed=10.0).title == "Track 2"


def test_previous_goes_back_when_pressed_early(queue):
    queue.next()
    assert queue.previous(elapsed=1.0).title == "Track 1"


def test_shuffle_keeps_the_current_track_playing(queue):
    queue.next()
    playing = queue.current
    queue.set_shuffle(True)
    assert queue.current is playing


def test_shuffle_reorders_what_is_coming(queue):
    queue.set_shuffle(True)
    assert [t.title for t in queue.upcoming] != ["Track 2", "Track 3", "Track 4", "Track 5"]
    assert len(queue.upcoming) == 4


def test_shuffle_plays_everything_exactly_once(queue):
    """A shuffled order, not a random pick — nothing repeats, nothing is skipped."""
    queue.set_shuffle(True)
    played = [queue.current.title]
    while (track := queue.next()) is not None:
        played.append(track.title)

    assert sorted(played) == sorted(f"Track {i}" for i in range(1, 6))
    assert len(played) == len(set(played))


def test_turning_shuffle_off_restores_the_order(queue):
    queue.set_shuffle(True)
    queue.set_shuffle(False)
    assert [t.title for t in queue.tracks] == [f"Track {i}" for i in range(1, 6)]
    assert queue.current.title == "Track 1"


def test_starting_a_shuffled_album_at_a_chosen_track_plays_that_track(queue):
    queue.set_shuffle(True)
    queue.set_tracks(make_tracks(5), start=3)
    assert queue.current.title == "Track 4"


def test_play_next_slots_in_after_the_current_track(queue):
    extra = Track(path="/music/x.mp3", title="Jumped the line")
    queue.play_next([extra])
    assert queue.upcoming[0].title == "Jumped the line"


def test_enqueue_goes_to_the_end(queue):
    extra = Track(path="/music/x.mp3", title="Last")
    queue.enqueue([extra])
    assert queue.upcoming[-1].title == "Last"


def test_repeat_cycles_through_its_modes():
    assert Repeat.OFF.next() is Repeat.ALL
    assert Repeat.ALL.next() is Repeat.ONE
    assert Repeat.ONE.next() is Repeat.OFF


def test_a_queue_round_trips():
    tracks = make_tracks(4)
    library = {t.path: t for t in tracks}

    queue = Queue(rng=random.Random(1))
    queue.set_tracks(tracks, start=2)
    queue.set_shuffle(True)
    queue.repeat = Repeat.ALL

    restored = Queue.from_dict(queue.to_dict(), library.get)
    assert restored.current.path == queue.current.path
    assert restored.shuffle and restored.repeat is Repeat.ALL


def test_a_saved_queue_survives_missing_files():
    """Tracks deleted since the queue was saved must not shift the others."""
    tracks = make_tracks(3)
    queue = Queue()
    queue.set_tracks(tracks)
    data = queue.to_dict()

    only_one = {tracks[0].path: tracks[0]}
    restored = Queue.from_dict(data, only_one.get)

    assert len(restored.tracks) == 1
    assert restored.current.path == tracks[0].path


# ── The library ───────────────────────────────────────────────────

def test_a_file_with_no_tags_is_still_a_track(tmp_path):
    path = tmp_path / "mystery.mp3"
    path.write_bytes(b"not really an mp3")

    track = read_track(path)
    assert track.title == "mystery"
    assert track.path == str(path)


def test_cover_art_beside_the_file_is_found(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(b"jpeg")
    song = tmp_path / "song.mp3"
    song.write_bytes(b"audio")

    assert read_track(song).cover.endswith("cover.jpg")


def test_scanning_finds_audio_and_ignores_everything_else(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.flac").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("nope")
    nested = tmp_path / "album"
    nested.mkdir()
    (nested / "c.opus").write_bytes(b"x")

    library = Library(folders=[str(tmp_path)])
    added, _removed = library.rescan()

    assert added == 3
    assert {t.file.name for t in library.all()} == {"a.mp3", "b.flac", "c.opus"}


def test_rescanning_drops_files_that_are_gone(tmp_path):
    song = tmp_path / "a.mp3"
    song.write_bytes(b"x")

    library = Library(folders=[str(tmp_path)])
    library.rescan()
    song.unlink()
    _added, removed = library.rescan()

    assert removed == 1
    assert not library.tracks


def test_downloads_are_not_removed_by_a_rescan(tmp_path):
    """A downloaded track outside the scanned folders must survive."""
    library = Library(folders=[str(tmp_path)])
    library.add(Track(path="/elsewhere/song.mp3", title="Kept", source="youtube"))
    library.rescan()

    assert library.track("/elsewhere/song.mp3") is not None


def test_albums_group_and_sort_by_track_number():
    library = Library()
    for number in (3, 1, 2):
        library.add(Track(path=f"/m/{number}.mp3", title=f"T{number}",
                          artist="A", album="Album", track_number=number))

    (_key, tracks), = library.albums().items()
    assert [t.track_number for t in tracks] == [1, 2, 3]


def test_search_covers_title_artist_and_album():
    library = Library()
    library.add(Track(path="/m/1.mp3", title="Blue", artist="Someone"))
    library.add(Track(path="/m/2.mp3", title="Other", album="Blue notes"))
    library.add(Track(path="/m/3.mp3", title="Nope"))

    assert len(library.search("blue")) == 2


def test_the_library_round_trips(tmp_path):
    path = tmp_path / "library.json"
    library = Library(folders=["/music"])
    library.add(Track(path="/m/1.mp3", title="One", play_count=4))
    library.save(path)

    restored = Library.load(path)
    assert restored.folders == ["/music"]
    assert restored.track("/m/1.mp3").play_count == 4


def test_a_corrupt_library_loads_empty(tmp_path):
    path = tmp_path / "library.json"
    path.write_text("{ not json")
    assert Library.load(path).tracks == {}


# ── Playlists ─────────────────────────────────────────────────────

def test_a_playlist_writes_extended_m3u(tmp_path):
    playlist = Playlist(title="Mix", tracks=make_tracks(2))
    text = playlist.to_m3u(tmp_path)

    assert text.startswith("#EXTM3U")
    assert "#PLAYLIST:Mix" in text
    assert "#EXTINF:180,Someone - Track 1" in text


def test_a_playlist_round_trips_through_a_file(tmp_path):
    library = Library()
    for track in make_tracks(3):
        library.add(track)

    store = PlaylistStore(tmp_path)
    playlist = store.create("Evening", library.all())

    restored = Playlist.from_m3u(playlist.path, library)
    assert restored.title == "Evening"
    assert len(restored.tracks) == 3


def test_missing_tracks_are_kept_with_the_playlist(tmp_path):
    """The whole point of the importer: the misses survive a restart."""
    library = Library()
    store = PlaylistStore(tmp_path)

    playlist = store.create("Imported")
    playlist.source = "spotify"
    playlist.missing = ["Boards of Canada - Everything You Do Is a Balloon"]
    store.save(playlist)

    restored = Playlist.from_m3u(playlist.path, library)
    assert restored.source == "spotify"
    assert restored.missing == ["Boards of Canada - Everything You Do Is a Balloon"]


def test_two_playlists_with_one_name_do_not_overwrite(tmp_path):
    store = PlaylistStore(tmp_path)
    one = store.create("Mix")
    two = store.create("Mix")
    assert one.path != two.path


def test_a_playlist_does_not_add_the_same_track_twice():
    playlist = Playlist(title="Mix")
    tracks = make_tracks(2)
    assert playlist.add(tracks) == 2
    assert playlist.add(tracks) == 0


def test_playlist_names_are_made_safe():
    assert safe_name("Songs / 2026: the best!") == "Songs 2026 the best"


# ── cava ──────────────────────────────────────────────────────────

def test_a_cava_frame_becomes_levels():
    levels = cava.parse_frame("0;500;1000;250;", bars=4)
    assert levels == [0.0, 0.5, 1.0, 0.25]


def test_a_short_frame_is_padded_not_rejected():
    assert len(cava.parse_frame("500;", bars=50)) == 50


def test_a_malformed_frame_does_not_raise():
    assert cava.parse_frame("500;oops;250;", bars=3) == [0.5, 0.0, 0.25]


def test_smoothing_averages_neighbours():
    smoothed = cava.smooth([0.0, 1.0, 0.0], window=1)
    assert smoothed[1] == pytest.approx(1 / 3)


def test_the_config_matches_the_quickshell_one():
    """The player and the desktop bar must be drawing the same numbers."""
    text = cava.CONFIG.format(framerate=60, bars=50, maximum=1000, noise=20)
    assert "mode = waves" in text
    assert "bars = 50" in text
    assert "channels = mono" in text
    assert "method = raw" in text


# ── Spotify import ────────────────────────────────────────────────

def test_a_playlist_id_is_found_in_any_link_shape():
    for link in (
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc",
        "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
    ):
        assert spotify.playlist_id(link) == "37i9dQZF1DXcBWIGoYBM5M"


def test_a_link_that_is_not_a_playlist_gives_nothing():
    assert spotify.playlist_id("https://example.com/nope") == ""


def test_a_pasted_list_is_read():
    tracks = spotify.from_text("Boards of Canada - Roygbiv\nAphex Twin - Xtal")
    assert [t.artist for t in tracks] == ["Boards of Canada", "Aphex Twin"]
    assert tracks[0].title == "Roygbiv"


def test_an_exportify_csv_is_read():
    csv_text = (
        "Track URI,Track Name,Artist Name(s),Album Name\n"
        "spotify:track:1,Roygbiv,Boards of Canada,Music Has the Right\n"
    )
    tracks = spotify.from_text(csv_text)
    assert len(tracks) == 1
    assert tracks[0].artist == "Boards of Canada"
    assert tracks[0].album == "Music Has the Right"


def test_matching_reports_hits_and_misses():
    tracks = [
        spotify.SpotifyTrack(title="Found", artist="A"),
        spotify.SpotifyTrack(title="Lost", artist="B"),
    ]

    def finder(title, _artist):
        return object() if title == "Found" else None

    report = spotify.match_all(tracks, finder)

    assert len(report.matched) == 1
    assert report.missed_lines() == ["B - Lost"]
    assert report.summary == "1 of 2 found · 1 missing"


def test_a_finder_that_raises_counts_as_a_miss():
    """One bad lookup must not abandon the rest of the playlist."""
    def finder(_title, _artist):
        raise RuntimeError("network died")

    report = spotify.match_all([spotify.SpotifyTrack(title="X")], finder)
    assert len(report.missed) == 1


def test_a_clean_import_says_so():
    report = spotify.match_all(
        [spotify.SpotifyTrack(title="A")], lambda _t, _a: object()
    )
    assert report.summary == "All 1 tracks found"


# ── The server ────────────────────────────────────────────────────

def test_subsonic_token_authentication():
    # md5("hunter2" + "salt") is what a client would send.
    import hashlib

    token = hashlib.md5(b"hunter2salt").hexdigest()
    assert check_token("hunter2", token, "salt")
    assert not check_token("hunter2", token, "different-salt")


def test_track_ids_are_stable_and_distinct():
    one = Track(path="/music/a.mp3")
    two = Track(path="/music/b.mp3")

    assert _track_id(one) == _track_id(Track(path="/music/a.mp3"))
    assert _track_id(one) != _track_id(two)


def test_server_config_round_trips():
    config = ServerConfig(enabled=True, port=9000, username="gavin")
    restored = ServerConfig.from_dict(json.loads(json.dumps(config.to_dict())))
    assert restored.enabled and restored.port == 9000 and restored.username == "gavin"


def test_a_silly_port_is_clamped():
    assert ServerConfig.from_dict({"port": 999999}).port == 65535


def test_no_password_means_open_access():
    from rose_bouquet.core.server import MusicServer

    server = MusicServer(library=Library(), config=ServerConfig())
    assert server.authorised({})


def test_a_password_is_required_when_set():
    from rose_bouquet.core.server import MusicServer

    server = MusicServer(library=Library(), config=ServerConfig(password="hunter2"))
    assert not server.authorised({})
    assert server.authorised({"p": ["hunter2"]})


# ── Long playlists ────────────────────────────────────────────────

def test_paging_reads_every_page_not_just_the_first():
    """The bug this fixes: a 250-track playlist importing as 100."""
    total = 250

    def fake_get(url, params):
        offset = params["offset"]
        limit = params["limit"]
        items = [
            {"track": {"name": f"Track {i}", "artists": [{"name": "Someone"}],
                       "album": {"name": "An album"}, "duration_ms": 180000}}
            for i in range(offset, min(offset + limit, total))
        ]
        return {"items": items, "total": total}

    tracks = spotify._page_tracks(fake_get, "abc")
    assert len(tracks) == total
    assert tracks[-1].title == "Track 249"


def test_paging_stops_when_a_page_comes_back_empty():
    def fake_get(_url, _params):
        return {"items": [], "total": 0}

    assert spotify._page_tracks(fake_get, "abc") == []


def test_paging_skips_removed_and_local_tracks():
    """Spotify returns empty entries for those; they are genuinely missing."""
    def fake_get(_url, params):
        if params["offset"]:
            return {"items": [], "total": 2}
        return {"items": [{"track": {"name": "Real", "artists": [{"name": "A"}]}},
                          {"track": None}], "total": 2}

    tracks = spotify._page_tracks(fake_get, "abc")
    assert [t.title for t in tracks] == ["Real"]


def test_exactly_one_page_is_flagged_as_probably_truncated():
    hundred = [spotify.SpotifyTrack(title=f"T{i}") for i in range(100)]
    assert spotify.looks_truncated(hundred)
    assert not spotify.looks_truncated(hundred[:99])
