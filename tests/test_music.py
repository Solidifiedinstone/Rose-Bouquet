"""The queue, the library, playlists, the cava bridge, Spotify import and the server."""

from __future__ import annotations

import json
import random
from types import SimpleNamespace

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


def test_the_frame_rate_is_configurable():
    """Not every machine can spare a core for decoration at 60fps."""
    assert "framerate = 24" in cava.CONFIG.format(
        framerate=cava.clamp_framerate(24), bars=50, maximum=1000, noise=20)


def test_an_absurd_frame_rate_is_clamped_rather_than_obeyed():
    assert cava.clamp_framerate(100000) == cava.MAX_FRAMERATE
    assert cava.clamp_framerate(0) == cava.MIN_FRAMERATE
    assert cava.clamp_framerate("nonsense") == cava.FRAMERATE


def test_the_cava_config_uses_the_expected_settings():
    """A bar configured the same way must draw the same numbers."""
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
    config = ServerConfig(enabled=True, port=9000, username="listener")
    restored = ServerConfig.from_dict(json.loads(json.dumps(config.to_dict())))
    assert restored.enabled and restored.port == 9000 and restored.username == "listener"


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


# ── Readability ───────────────────────────────────────────────────

def test_every_theme_is_readable():
    """No theme may ship with text you cannot read.

    The palettes come from other people — Dracula, Solarized, Catppuccin — and
    their dim colours are chosen for their own background, not for the panels
    and hover rows this app also draws them on. Rather than editing someone
    else's palette, the stylesheet nudges the colour just far enough to clear
    the WCAG bar; this test is what stops that quietly regressing.
    """
    from rose_bouquet.ui.theme import THEMES, contrast, luminance, readable

    unreadable = []
    for theme in THEMES.values():
        surfaces = (theme.background, theme.panel, theme.elevated)

        hardest_dim = min(surfaces, key=lambda s: contrast(theme.text_dim, s))
        dim = readable(theme.text_dim, hardest_dim, toward=theme.text, target=3.0)

        hardest_text = min(surfaces, key=lambda s: contrast(theme.text, s))
        away = "#ffffff" if luminance(theme.text) >= luminance(hardest_text) else "#000000"
        body = readable(theme.text, hardest_text, toward=away, target=4.5)

        for surface in surfaces:
            if contrast(dim, surface) < 3.0:
                unreadable.append(f"{theme.name}: dim {contrast(dim, surface):.1f}:1")
            if contrast(body, surface) < 4.5:
                unreadable.append(f"{theme.name}: text {contrast(body, surface):.1f}:1")

    assert not unreadable, "; ".join(unreadable)


def test_a_colour_that_already_passes_is_left_alone():
    """A carefully drawn theme must come out exactly as its author drew it."""
    from rose_bouquet.ui.theme import readable

    assert readable("#ffffff", "#000000", toward="#000000", target=4.5) == "#ffffff"


def test_contrast_matches_the_wcag_extremes():
    from rose_bouquet.ui.theme import contrast

    assert contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.1)
    assert contrast("#777777", "#777777") == pytest.approx(1.0, abs=0.01)


# ── Long playlists, read in pages ─────────────────────────────────

class FakeSpotify:
    """A playlist of any length, optionally rate-limiting part way through."""

    def __init__(self, total, cut_at=None):
        self.total = total
        self.cut_at = cut_at
        self.calls = []

    def page(self, playlist_id, token, offset=0, limit=100):
        from rose_bouquet.core.spotify import Page, SpotifyTrack

        self.calls.append(offset)
        if self.cut_at is not None and offset >= self.cut_at:
            return Page(next_offset=offset, retry_after=60, total=self.total,
                        error="Spotify is rate-limiting this connection")

        end = min(offset + limit, self.total)
        tracks = [SpotifyTrack(title=f"Track {i}", artist="Someone") for i in range(offset, end)]
        finished = end >= self.total
        return Page(tracks=tracks, total=self.total,
                    next_offset=None if finished else end)


def test_a_long_playlist_is_read_in_full(monkeypatch):
    """250 tracks must arrive as 250, not as the first hundred."""
    fake = FakeSpotify(total=250)
    monkeypatch.setattr(spotify, "fetch_page", fake.page)
    monkeypatch.setattr(spotify, "client_token", lambda *a, **k: "token")

    page = spotify.read_all("https://open.spotify.com/playlist/abcdefghijklmnopqrst")

    assert len(page.tracks) == 250
    assert page.next_offset is None
    assert fake.calls == [0, 100, 200]


def test_being_cut_off_keeps_the_place(monkeypatch):
    """Rate-limited at 200: keep the 200 and remember to carry on from there."""
    fake = FakeSpotify(total=900, cut_at=200)
    monkeypatch.setattr(spotify, "fetch_page", fake.page)
    monkeypatch.setattr(spotify, "client_token", lambda *a, **k: "token")

    page = spotify.read_all("https://open.spotify.com/playlist/abcdefghijklmnopqrst")

    assert len(page.tracks) == 200
    assert page.next_offset == 200
    assert page.retry_after == 60
    assert page.total == 900


def test_carrying_on_starts_where_it_stopped(monkeypatch):
    fake = FakeSpotify(total=250)
    monkeypatch.setattr(spotify, "fetch_page", fake.page)
    monkeypatch.setattr(spotify, "client_token", lambda *a, **k: "token")

    page = spotify.read_all("https://open.spotify.com/playlist/abcdefghijklmnopqrst",
                            start_offset=200)

    assert [t.title for t in page.tracks] == [f"Track {i}" for i in range(200, 250)]
    assert fake.calls == [200]


def test_an_import_record_survives_a_cut_off_read(tmp_path):
    """The offset has to outlive the process, or 'carry on' is a lie."""
    from rose_bouquet.core.imports import ImportJob

    job = ImportJob(title="Long one", link="https://open.spotify.com/playlist/abc")
    job.add_tracks([spotify.SpotifyTrack(title=f"T{i}") for i in range(200)])
    job.next_offset = 200
    job.expected_total = 900
    path = job.save(tmp_path)

    restored = ImportJob.load(path)
    assert restored.next_offset == 200
    assert restored.expected_total == 900
    assert not restored.fully_read
    assert not restored.finished          # not done: there is more to read
    assert "700 still to read" in restored.summary


def test_a_fully_read_playlist_is_finished_once_downloaded(tmp_path):
    from rose_bouquet.core.imports import ImportJob

    job = ImportJob(title="Short one")
    job.add_tracks([spotify.SpotifyTrack(title="Only one")])
    job.note_match(job.entries[0], "vid")
    job.note_done("vid", "/music/a.mp3")

    assert job.fully_read and job.finished


def test_a_rate_limit_window_is_remembered_and_explained(tmp_path):
    """Being told to come back in a day must read as a wait, not a failure."""
    from datetime import datetime, timedelta

    from rose_bouquet.core.imports import ImportJob

    job = ImportJob(title="Long one", link="https://open.spotify.com/playlist/abc")
    job.add_tracks([spotify.SpotifyTrack(title=f"T{i}") for i in range(100)])
    job.next_offset = 100
    job.expected_total = 900
    job.block_for(6 * 3600)

    assert 5.9 * 3600 < job.wait_remaining() < 6.1 * 3600
    assert job.summary.startswith("Spotify is rate-limiting this connection")
    assert "5h 5" in job.summary or "6 hours" in job.summary

    restored = ImportJob.load(job.save(tmp_path))
    assert restored.wait_remaining() > 0

    # Once the window passes it is simply gone.
    restored.blocked_until = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    assert restored.wait_remaining() == 0


def test_the_wait_can_be_overridden_after_changing_network():
    """The limit is on the connection, so our note about it must be dismissable."""
    from rose_bouquet.core.imports import ImportJob

    job = ImportJob(title="Long one")
    job.block_for(86400)
    assert job.wait_remaining() > 0

    job.blocked_until = ""            # what "Try now" does
    assert job.wait_remaining() == 0


def test_the_login_entry_names_an_absolute_command(tmp_path, monkeypatch):
    """A desktop entry does not inherit a shell's PATH.

    A bare command name works when tested from a terminal and then silently
    fails at login, which is the trap the app's own desktop entry documents.
    """
    from rose_bouquet.core import autostart

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert not autostart.enabled()

    written = autostart.enable()
    assert written.exists()
    assert autostart.enabled()

    body = written.read_text()
    exec_line = next(line for line in body.splitlines() if line.startswith("Exec="))
    command = exec_line.removeprefix("Exec=").split()[0]
    assert command.startswith("/"), exec_line
    assert "--serve-only" in exec_line
    # No window: it must not turn up in a dock or a task switcher.
    assert "NoDisplay=true" in body

    autostart.disable()
    assert not autostart.enabled()
    assert not written.exists()


def test_turning_the_login_entry_off_twice_is_not_an_error(tmp_path, monkeypatch):
    from rose_bouquet.core import autostart

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    autostart.disable()
    autostart.disable()


def test_version_comparison_treats_versions_as_numbers():
    """A string comparison calls 0.1.10 older than 0.1.9.

    Which breaks precisely when a project has had enough releases for updates
    to matter.
    """
    from rose_bouquet.core.updates import is_newer

    assert is_newer("0.1.10", "0.1.9")
    assert is_newer("0.2.0", "0.1.99")
    assert is_newer("1.0.0", "0.9.9")
    assert not is_newer("0.1.2", "0.1.3")
    assert not is_newer("0.1.3", "0.1.3")
    # Tags carry a leading v and releases sometimes a suffix.
    assert is_newer("v0.1.4", "0.1.3")
    assert not is_newer("0.1.3-beta", "0.1.3")


def test_fullscreen_returns_the_picture_to_the_stage():
    """Reparenting a live video widget has to be undone exactly.

    Fullscreen moves the picture into a frameless window of its own — maximising
    the stage instead would carry the window chrome and the transport with it,
    which is what people mean when they say fullscreen does not work. The risk
    is the way back: leave the widget in the dead window and the stage is left
    with a hole where the video was.

    Builds a real widget, which nothing else here does, because reparenting is
    the whole behaviour and there is no part of it left once Qt is mocked out.
    Offscreen, so it needs no display.
    """
    import os

    # Offscreen is forced in conftest.py, before Qt is imported anywhere.
    from PySide6.QtWidgets import QApplication

    from rose_bouquet.ui.theme import Appearance
    from rose_bouquet.ui.video import VideoStage

    app = QApplication.instance() or QApplication([])
    stage = VideoStage(youtube=None, appearance=Appearance())
    try:
        home = stage.video.parent()
        assert stage._fullscreen_window is None

        stage.toggle_fullscreen()
        assert stage._fullscreen_window is not None
        assert stage.video.parent() is not home

        stage.leave_fullscreen()
        assert stage._fullscreen_window is None
        assert stage.video.parent() is home

        # A round trip must not leave a second window or lose the picture.
        stage.toggle_fullscreen()
        stage.toggle_fullscreen()
        assert stage._fullscreen_window is None
        assert stage.video.parent() is home

        # And leaving when it was never entered is a no-op, not a crash.
        stage.leave_fullscreen()
    finally:
        stage.deleteLater()
        app.processEvents()





# ── Loading an Exportify CSV from disk ────────────────────────────

def _import_view():
    """A real ImportView, offscreen."""
    import os

    # Offscreen is forced in conftest.py, before Qt is imported anywhere.
    from PySide6.QtWidgets import QApplication

    from rose_bouquet.ui.theme import Appearance
    from rose_bouquet.ui.views import ImportView

    app = QApplication.instance() or QApplication([])
    return app, ImportView(Appearance())


EXPORTIFY_CSV = (
    "Track URI,Track Name,Artist Name(s),Album Name\n"
    "spotify:track:1,Bad Guy,Billie Eilish,When We All Fall Asleep\n"
    "spotify:track:2,Alright,Kendrick Lamar,To Pimp a Butterfly\n"
)


def test_a_chosen_csv_lands_in_the_box_the_parser_reads(tmp_path):
    """The button is a shortcut into the paste box, not a second code path.

    Exportify hands you a file and the box only took text, so the file had to
    be opened in an editor and copied by hand. What the button must not do is
    grow a private parser: it fills the same box, and the same reader answers.
    """
    csv_file = tmp_path / "playlist.csv"
    csv_file.write_text(EXPORTIFY_CSV, encoding="utf-8")

    app, view = _import_view()
    try:
        view._load_csv(str(csv_file))
        tracks = spotify.from_text(view.paste.toPlainText())
        assert [(t.artist, t.title) for t in tracks] == [
            ("Billie Eilish", "Bad Guy"),
            ("Kendrick Lamar", "Alright"),
        ]
    finally:
        view.deleteLater()
        app.processEvents()


def test_the_byte_order_mark_exportify_writes_does_not_hide_the_header(tmp_path):
    """Exportify writes a BOM, and the header is what marks the file a CSV.

    Read as plain utf-8 the BOM sticks to the first column name, the header no
    longer matches, and the whole file is taken for a list of song titles —
    every row silently becomes a track called "Track URI,Track Name,…". Read as
    utf-8-sig it is the same file as the one above.
    """
    csv_file = tmp_path / "bom.csv"
    csv_file.write_bytes(b"\xef\xbb\xbf" + EXPORTIFY_CSV.encode("utf-8"))

    app, view = _import_view()
    try:
        view._load_csv(str(csv_file))
        assert not view.paste.toPlainText().startswith("﻿")
        tracks = spotify.from_text(view.paste.toPlainText())
        assert [t.title for t in tracks] == ["Bad Guy", "Alright"]
    finally:
        view.deleteLater()
        app.processEvents()


def test_an_unreadable_or_empty_csv_is_said_out_loud(tmp_path):
    """A bad file gets a sentence, not a traceback and not silence."""
    app, view = _import_view()
    said = []
    view.status.connect(lambda text, kind: said.append((text, kind)))
    try:
        view._load_csv(str(tmp_path / "does-not-exist.csv"))
        assert said and said[-1][1] == "warning"

        empty = tmp_path / "empty.csv"
        empty.write_text("   \n", encoding="utf-8")
        view._load_csv(str(empty))
        assert "empty" in said[-1][0].lower()

        # Neither attempt may leave half a file in the box.
        assert not view.paste.toPlainText().strip()
    finally:
        view.deleteLater()
        app.processEvents()


def test_the_csv_button_stops_while_an_import_is_running():
    """Loading a second playlist mid-import would overwrite the first."""
    app, view = _import_view()
    try:
        assert view.csv_button.isEnabled()
        view.busy = True
        view.refresh()
        assert not view.csv_button.isEnabled()
        view.busy = False
        view.refresh()
        assert view.csv_button.isEnabled()
    finally:
        view.deleteLater()
        app.processEvents()


# ── What did not end up as audio ──────────────────────────────────

def test_a_failed_download_joins_the_tracks_nothing_was_found_for():
    """Two reasons, one list — because the consequence is the same.

    A song nobody could match and a song that matched but would not download
    are both songs you do not have. The second used to exist only as a toast
    and a state on a row in another tab, so it was the one that got lost.
    """
    found = spotify.SpotifyTrack(title="Alright", artist="Kendrick Lamar")
    lost = spotify.SpotifyTrack(title="Obscure B-Side", artist="Nobody")
    report = spotify.ImportReport(
        matched=[(found, object())], missed=[lost])

    assert report.missed_lines() == [str(lost)]

    report.note_download_failure(found, "HTTP 403")
    lines = report.missed_lines()
    assert len(lines) == 2
    assert str(lost) in lines[0]
    assert "Alright" in lines[1] and "403" in lines[1]
    assert "1 missing" in report.summary and "1 failed to download" in report.summary


def test_the_same_track_failing_twice_is_still_one_line():
    """A retry that fails again must not double the list."""
    track = spotify.SpotifyTrack(title="A", artist="B")
    report = spotify.ImportReport(matched=[(track, object())])

    report.note_download_failure(track, "timed out")
    report.note_download_failure(track, "timed out again")

    assert len(report.failed) == 1
    assert "timed out again" in report.missed_lines()[0]


def test_a_retry_that_works_takes_the_track_off_the_list():
    """Otherwise the list still accuses you of missing a song you have."""
    track = spotify.SpotifyTrack(title="A", artist="B")
    report = spotify.ImportReport(matched=[(track, object())])

    report.note_download_failure(track, "timed out")
    assert report.failed

    report.note_download_recovered(track)
    assert not report.failed
    assert report.missed_lines() == []
    assert report.summary == "All 1 tracks found"


def test_a_clean_import_still_says_so_with_the_failed_list_empty():
    """The happy path must not gain a new way to look unhappy."""
    report = spotify.ImportReport(matched=[(spotify.SpotifyTrack(title="A"), object())])
    assert report.summary == "All 1 tracks found"
    assert report.missed_lines() == []


# ── Retrying a download ───────────────────────────────────────────

def test_the_retry_button_hands_back_something_the_window_can_download():
    """The bug this pins: the row emits a DownloadRequest, and the slot it was
    wired to read `.id` — a field only a search Result has. Every press raised
    inside the signal, so the button looked dead and said nothing.

    Asserted on the field names rather than on a mocked window, because the
    mismatch between the two shapes *is* the fault.
    """
    from rose_bouquet.core import ytmusic

    request = ytmusic.DownloadRequest(video_id="abc123", title="T", artist="A")

    assert not hasattr(request, "id")
    assert request.video_id == "abc123"

    import inspect

    from rose_bouquet.ui.main_window import MainWindow

    # retry_download must take the request as it is, and must not reach for the
    # attribute that broke it.
    source = inspect.getsource(MainWindow.retry_download)
    assert "request.video_id" in source
    assert "request.id" not in source


def test_a_failed_row_keeps_the_request_so_it_can_be_retried():
    """A row with no payload shows no Retry button — nothing to send."""
    import os

    # Offscreen is forced in conftest.py, before Qt is imported anywhere.
    from PySide6.QtWidgets import QApplication

    from rose_bouquet.core import ytmusic
    from rose_bouquet.ui.theme import Appearance
    from rose_bouquet.ui.views import DownloadsView

    app = QApplication.instance() or QApplication([])
    view = DownloadsView(Appearance())
    try:
        request = ytmusic.DownloadRequest(video_id="k", title="T")
        view.note("k", "A — T", 0.0, "failed", request)

        # note() repaints on a timer, which needs an event loop; the rows are
        # what is being tested, so they are built directly.
        view.refresh()

        sent = []
        view.retry_requested.connect(sent.append)
        view.rows["k"]["retry"].click()

        # The request must arrive intact — rebuilding it is how it broke.
        assert sent == [request]
    finally:
        view.deleteLater()
        app.processEvents()


# ── Saying that an update exists ──────────────────────────────────

def _update_bar():
    import os

    # Offscreen is forced in conftest.py, before Qt is imported anywhere.
    from PySide6.QtWidgets import QApplication

    from rose_bouquet.ui.theme import Appearance
    from rose_bouquet.ui.widgets import UpdateBar

    app = QApplication.instance() or QApplication([])
    return app, UpdateBar(Appearance())


def test_the_update_notice_stays_until_it_is_answered():
    """The reason this is not a Banner.

    A Banner is a toast — it hides itself after six seconds. An update notice
    that disappears while you are looking at the library is the same as never
    having shown it, which is how this worked before: a button in a settings
    tab you had to already know about.
    """
    app, bar = _update_bar()
    try:
        assert bar.isHidden()

        bar.announce("0.3.0")
        assert not bar.isHidden()
        assert "0.3.0" in bar.message.text()

        # No timer may take it away — only a press.
        app.processEvents()
        assert not bar.isHidden()

        bar.later.click()
        assert bar.isHidden()
    finally:
        bar.deleteLater()
        app.processEvents()


def test_the_bar_asks_to_update_and_then_stops_asking():
    """Pressing Update must fire once and disable itself, not queue five."""
    app, bar = _update_bar()
    try:
        asked = []
        bar.update_requested.connect(lambda: asked.append(1))

        bar.announce("0.3.0")
        bar.update_button.click()
        assert asked == [1]

        bar.working()
        assert not bar.update_button.isEnabled()
        bar.update_button.click()
        assert asked == [1]

        bar.finished("Updated. Restart to use it.")
        assert "Restart" in bar.message.text()
        assert bar.later.text() == "Close"
    finally:
        bar.deleteLater()
        app.processEvents()


def test_a_check_that_fails_says_nothing_on_its_own():
    """The launch check is unasked-for, so a machine offline must stay quiet.

    Settings → About keeps the button that does report what went wrong; this
    one running by itself must not complain on every launch.
    """
    import inspect

    from rose_bouquet.ui.main_window import MainWindow

    source = inspect.getsource(MainWindow.check_for_updates)
    # The failure path logs; it must not reach for the banner or a dialog.
    assert "logger" in source
    assert "self.notify" not in source
    assert "announce" in source


# ── The library shows the library ─────────────────────────────────

def _library_view(count: int):
    import os

    # Offscreen is forced in conftest.py, before Qt is imported anywhere.
    from PySide6.QtWidgets import QApplication

    from rose_bouquet.ui.theme import Appearance
    from rose_bouquet.ui.views import LibraryView

    app = QApplication.instance() or QApplication([])
    library = Library()
    for i in range(count):
        library.add(Track(path=f"/m/{i}.mp3", title=f"Song {i}",
                          artist="A", album="Al", duration=200))
    view = LibraryView(library, Appearance())
    view.resize(900, 700)
    return app, view


def test_a_long_library_is_all_reachable():
    """It used to stop at 500 and tell you to search.

    The cap was about build cost, but it was written as a claim about the
    person — nobody scrolls that far — and someone scrolling to the bottom
    plainly wanted what was down there. Every track is in the list now; the
    widgets are built in blocks as the scroll reaches them.
    """
    app, view = _library_view(915)
    try:
        view.refresh()

        assert view.count.text() == "915 tracks"
        assert len(view._tracks) == 915
        # Not all at once — that stall is why the cap existed.
        assert view._built < 915

        while view._built < len(view._tracks):
            view._extend()
        assert view._built == 915
    finally:
        view.deleteLater()
        app.processEvents()


def test_nothing_tells_you_to_search_to_see_your_own_music():
    """The "…and 415 more" line must not come back."""
    app, view = _library_view(915)
    try:
        view.refresh()
        while view._built < len(view._tracks):
            view._extend()

        texts = []
        for i in range(view.body_layout.count()):
            widget = view.body_layout.itemAt(i).widget()
            if widget is not None and hasattr(widget, "text"):
                texts.append(widget.text())

        assert not any("more" in t and "Search" in t for t in texts)
    finally:
        view.deleteLater()
        app.processEvents()


def test_playing_a_track_queues_the_whole_result_not_the_built_part():
    """Press play on row three and the queue is every track, not the first block.

    The rows are built lazily, so a queue taken from "what is on screen" would
    silently shrink to a block — the failure the lazy list could easily cause.
    """
    app, view = _library_view(400)
    try:
        view.refresh()
        assert view._built < 400

        queued = []
        view.play_requested.connect(lambda track, tracks: queued.append(tracks))

        first_row = view.body_layout.itemAt(0).widget()
        first_row.play_requested.emit(view._tracks[0])

        assert len(queued[0]) == 400
    finally:
        view.deleteLater()
        app.processEvents()


def test_the_launch_check_asks_once_a_day_not_once_a_launch():
    """An unasked-for network call needs both a limit and an off switch.

    Opening the app five times in an afternoon is one request. The preference
    exists because an app should not phone anywhere unprompted with no way to
    stop it, and the isolated startup test is entitled to a quiet process.
    """
    import time

    from rose_bouquet.ui.main_window import UPDATE_CHECK_INTERVAL
    from rose_bouquet.ui.preferences import Preferences

    prefs = Preferences()
    assert prefs.check_updates_on_start is True
    assert prefs.last_update_check == 0.0
    assert UPDATE_CHECK_INTERVAL == 24 * 60 * 60

    # Round trips, or the throttle forgets across launches and is no throttle.
    prefs.last_update_check = time.time()
    prefs.check_updates_on_start = False
    restored = Preferences.from_dict(prefs.to_dict())
    assert restored.check_updates_on_start is False
    assert restored.last_update_check == prefs.last_update_check


# ── What an album actually contains ───────────────────────────────

def test_a_tracklist_lays_your_files_into_the_real_album():
    """An album you have four tracks of is not a four-track album.

    The library only knew about files, so there was no way to tell an EP from
    half a record — the one thing you want to know while looking at it.
    """
    from rose_bouquet.core import musicbrainz as mb

    release = mb.Release(title="An Album", artist="A", tracks=[
        mb.CatalogueTrack(position=1, title="One", duration=100),
        mb.CatalogueTrack(position=2, title="Two", duration=200),
        mb.CatalogueTrack(position=3, title="Three", duration=300),
    ])
    owned = [Track(path="/m/2.mp3", title="Two", artist="A", album="An Album")]

    slots = mb.reconcile(release, owned)

    assert len(slots) == 3
    assert [s.owned for s in slots] == [False, True, False]
    assert slots[1].track.path == "/m/2.mp3"
    # The catalogue supplies length even for tracks you do not have.
    assert slots[0].duration == 100


def test_files_are_matched_by_title_not_by_position():
    """A file tagged with the wrong track number is common; a wrong title is not."""
    from rose_bouquet.core import musicbrainz as mb

    release = mb.Release(tracks=[
        mb.CatalogueTrack(position=1, title="Wesley's Theory"),
        mb.CatalogueTrack(position=2, title="For Free?"),
    ])
    # Tagged as track 3, prefixed by the ripper, and accented differently.
    owned = [Track(path="/m/x.mp3", title="03 - Wesley’s Theory", track_number=3)]

    slots = mb.reconcile(release, owned)
    assert slots[0].owned and not slots[1].owned


def test_a_track_the_catalogue_does_not_list_is_kept_not_hidden():
    """Losing a track you own would be far worse than an extra line."""
    from rose_bouquet.core import musicbrainz as mb

    release = mb.Release(tracks=[mb.CatalogueTrack(position=1, title="One")])
    owned = [Track(path="/m/1.mp3", title="One"),
             Track(path="/m/b.mp3", title="Some Bonus Track")]

    slots = mb.reconcile(release, owned)

    assert len(slots) == 2
    assert slots[-1].title == "Some Bonus Track"
    assert slots[-1].owned


def test_no_tracklist_means_the_album_looks_exactly_as_it_did():
    """A failed lookup must never cost you the view of your own music."""
    from rose_bouquet.core import musicbrainz as mb

    owned = [Track(path="/m/1.mp3", title="One"), Track(path="/m/2.mp3", title="Two")]

    slots = mb.reconcile(None, owned)

    assert len(slots) == 2
    assert all(s.owned for s in slots)
    assert [s.position for s in slots] == [1, 2]


def test_titles_compare_past_the_noise_rippers_add():
    from rose_bouquet.core import musicbrainz as mb

    same = "king kunta"
    for written in ("King Kunta", "03 - King Kunta", "King Kunta (Remastered)",
                    "King  Kunta", "King Kunta [Explicit]"):
        assert mb.normalise(written) == same

    # It must not collapse songs that genuinely differ.
    assert mb.normalise("One") != mb.normalise("One More Time")


# ── Where a tracklist comes from ──────────────────────────────────

class _FakeYTM:
    """Stands in for YouTube Music, including its confident wrong answers."""

    def __init__(self, hits, tracks=()):
        self._hits, self._tracks = hits, list(tracks)
        self.asked = []

    def search(self, query, kind=None, limit=25):
        self.asked.append((query, kind))
        return self._hits

    def album(self, browse_id):
        return "An Album", self._tracks


class _Hit:
    def __init__(self, title, artist, browse_id="b1"):
        self.title, self.artist, self.browse_id = title, artist, browse_id


class _Song:
    def __init__(self, title, duration=0):
        self.title, self.duration = title, duration


def test_youtube_music_fills_in_what_musicbrainz_has_never_heard_of(monkeypatch):
    """A library of netlabel and self-released records is mostly not catalogued.

    MusicBrainz is a catalogue of published releases, so for a lot of this
    library it simply says no — and the album then looks exactly as it did
    before the feature existed, which reads as broken.
    """
    from rose_bouquet.core import tracklists

    monkeypatch.setattr(tracklists.musicbrainz, "tracklist",
                        lambda artist, album: None)

    ytm = _FakeYTM([_Hit("golden dogs", "xe1la")],
                   [_Song("still"), _Song("wisdom"), _Song("angler fish")])

    release = tracklists.lookup("xe1la", "golden dogs", ytm)

    assert release is not None
    assert [t.title for t in release.tracks] == ["still", "wisdom", "angler fish"]
    assert [t.position for t in release.tracks] == [1, 2, 3]


def test_a_confidently_wrong_album_is_refused(monkeypatch):
    """Searching YouTube Music for "Acoustin / Acoustin" really does return
    "Black & White (Acoustic)" by Meechi Mono. A tracklist from the wrong
    record is worse than none: it invents songs and offers to download them.
    """
    from rose_bouquet.core import tracklists

    monkeypatch.setattr(tracklists.musicbrainz, "tracklist",
                        lambda artist, album: None)

    ytm = _FakeYTM([_Hit("Black & White (Acoustic)", "Meechi Mono")],
                   [_Song("Black & White")])

    assert tracklists.lookup("Acoustin", "Acoustin", ytm) is None


def test_musicbrainz_is_asked_first_and_youtube_music_not_at_all(monkeypatch):
    """The catalogue's track numbers are the release's own — prefer them."""
    from rose_bouquet.core import musicbrainz, tracklists

    known = musicbrainz.Release(title="An Album", tracks=[
        musicbrainz.CatalogueTrack(position=1, title="One")])
    monkeypatch.setattr(tracklists.musicbrainz, "tracklist",
                        lambda artist, album: known)

    ytm = _FakeYTM([_Hit("An Album", "A")], [_Song("Something Else")])

    assert tracklists.lookup("A", "An Album", ytm) is known
    assert ytm.asked == []


def test_no_source_knows_it_and_nothing_pretends_otherwise(monkeypatch):
    from rose_bouquet.core import tracklists

    monkeypatch.setattr(tracklists.musicbrainz, "tracklist",
                        lambda artist, album: None)

    assert tracklists.lookup("Nobody", "Nothing", _FakeYTM([])) is None
    # And with no YouTube Music at all — the offline install.
    assert tracklists.lookup("Nobody", "Nothing", None) is None


# ── Playback gives up instead of tearing through the library ──────

def _playback():
    """A real Playback over an empty library, offscreen."""
    # Offscreen is forced in conftest.py, before Qt is imported anywhere.
    from PySide6.QtWidgets import QApplication

    from rose_bouquet.ui.playback import Playback

    app = QApplication.instance() or QApplication([])
    return Playback(Library()), app


def test_a_missing_drive_is_named_not_the_file_on_it(tmp_path):
    from rose_bouquet.ui.playback import Playback

    gone = tmp_path / "drive" / "Music" / "Someone - A song.mp3"
    # The outermost folder that vanished is the one worth naming.
    assert Playback._missing_folder(str(gone)) == str(tmp_path / "drive")


def test_a_single_missing_file_does_not_accuse_the_drive(tmp_path):
    from rose_bouquet.ui.playback import Playback

    assert Playback._missing_folder(str(tmp_path / "gone.mp3")) is None
    (tmp_path / "here.mp3").write_bytes(b"")
    assert Playback._missing_folder(str(tmp_path / "here.mp3")) is None


def test_one_bad_file_is_skipped_but_a_run_of_them_stops_playback(tmp_path):
    from PySide6.QtMultimedia import QMediaPlayer

    from rose_bouquet.ui.playback import FAILURE_LIMIT, Playback

    playback, app = _playback()
    try:
        playback.queue.set_tracks([
            Track(path=str(tmp_path / "drive" / "Music" / f"{i}.mp3"), title=f"T{i}",
                  artist="Someone", album="An album", duration=180, track_number=i)
            for i in range(1, 20)
        ])

        reasons: list[str] = []
        playback.failed.connect(reasons.append)

        # Under the limit, the queue steps past the dead files and says nothing.
        for _ in range(FAILURE_LIMIT - 1):
            playback._on_error(QMediaPlayer.Error.ResourceError, "Could not open file")
        assert reasons == []

        # At the limit it stops, once, naming the folder that is missing.
        playback._on_error(QMediaPlayer.Error.ResourceError, "Could not open file")
        assert len(reasons) == 1
        assert str(tmp_path / "drive") in reasons[0]

        # And having stopped, it is not still counting down to a second report.
        assert playback._failures == 0
    finally:
        playback.deleteLater()
        app.processEvents()


def test_a_track_that_opens_clears_the_failure_count():
    from PySide6.QtMultimedia import QMediaPlayer

    from rose_bouquet.ui.playback import Playback

    playback, app = _playback()
    try:
        playback._failures = 2
        playback._on_status(QMediaPlayer.MediaStatus.LoadedMedia)
        # Two unplayable files scattered through an evening are not a run.
        assert playback._failures == 0
    finally:
        playback.deleteLater()
        app.processEvents()


# ── An unmounted drive is not an emptied library ──────────────────

def test_a_missing_folder_does_not_empty_the_library(tmp_path):
    """The failure that started this: a drive that came up under a new name.

    `/dev/sdb1` in fstab named one disk on Monday and a different one on
    Friday, so the music folder was simply not there. A scan that treated
    "cannot read the folder" as "the folder is empty" would have deleted the
    whole library and every play count in it.
    """
    music = tmp_path / "drive" / "Music"
    music.mkdir(parents=True)
    (music / "a.mp3").write_bytes(b"x")
    (music / "b.mp3").write_bytes(b"x")

    library = Library(folders=[str(music)])
    assert library.rescan() == (2, 0)
    library.track(str(music / "a.mp3")).play_count = 7

    # The drive goes away — the folder with it.
    for track in list(music.iterdir()):
        track.unlink()
    music.rmdir()

    added, removed = library.rescan()
    assert (added, removed) == (0, 0)
    assert len(library.tracks) == 2
    assert library.track(str(music / "a.mp3")).play_count == 7
    assert library.missing_roots() == [music]

    # And when it comes back, the library is simply itself again.
    music.mkdir(parents=True)
    (music / "a.mp3").write_bytes(b"x")
    (music / "b.mp3").write_bytes(b"x")
    assert library.rescan() == (0, 0)
    assert library.missing_roots() == []


def test_a_folder_that_is_there_still_loses_its_deleted_files(tmp_path):
    """The guard is about unreadable folders, not about never pruning."""
    library = Library(folders=[str(tmp_path)])
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.mp3").write_bytes(b"x")
    library.rescan()

    (tmp_path / "b.mp3").unlink()
    _added, removed = library.rescan()

    assert removed == 1
    assert list(library.tracks) == [str(tmp_path / "a.mp3")]


def test_a_track_outside_every_folder_is_still_pruned(tmp_path):
    """Dropping a folder from settings still drops the tracks that were in it."""
    old = tmp_path / "old"
    old.mkdir()
    library = Library(folders=[str(old)])
    library.add(Track(path=str(old / "gone.mp3"), title="Gone", source="local"))

    # The folder is no longer scanned, and it is not missing either — it is
    # simply not ours any more.
    library.folders = [str(tmp_path / "new")]
    (tmp_path / "new").mkdir()
    _added, removed = library.rescan()

    assert removed == 1
    assert not library.tracks


# ── An import fetches what is not actually there ──────────────────

def _spotify_job(tmp_path, count=3):
    from rose_bouquet.core import imports
    job = imports.ImportJob(title="Tunes")
    job.add_tracks([
        SimpleNamespace(title=f"Song {i}", artist="Somebody", album="An album",
                        duration=180)
        for i in range(count)
    ])
    return job


def test_a_library_entry_whose_file_is_gone_is_not_evidence_of_a_download(tmp_path):
    """The failure that made an import of 909 tracks download none of them.

    Every row matched, every row was marked already-downloaded off the back of
    a library that still listed the tracks, and not one file was fetched — the
    files those library entries named had been deleted.
    """
    from rose_bouquet.core import imports

    job = _spotify_job(tmp_path)
    library = Library()
    for index in range(3):
        library.add(Track(path=str(tmp_path / f"{index}.mp3"),
                          title=f"Song {index}", artist="Somebody"))

    # Nothing is on disk, so nothing counts as already had — every row is
    # still work to do rather than a tick.
    assert job.skip_already_downloaded(library) == 0
    assert job.count(imports.DONE) == 0

    # One of them really is there; that one, and only that one, is skipped.
    (tmp_path / "1.mp3").write_bytes(b"x")
    assert job.skip_already_downloaded(library) == 1
    assert job.count(imports.DONE) == 1
    assert [e.title for e in job.entries if e.state == imports.DONE] == ["Song 1"]


def test_a_finished_import_asks_again_for_files_that_have_gone(tmp_path):
    from rose_bouquet.core import imports

    job = _spotify_job(tmp_path)
    for index, entry in enumerate(job.entries):
        entry.state = imports.DONE
        entry.video_id = f"vid{index}"
        entry.path = str(tmp_path / f"{index}.mp3")
    (tmp_path / "2.mp3").write_bytes(b"x")

    # The two whose files went come back as work; the one still there does not.
    assert job.forget_downloads_that_are_gone() == 2
    assert job.count(imports.DONE) == 1
    # And they keep the match, so nothing is looked up on Spotify twice.
    assert all(e.video_id for e in job.pending())
    assert {e.state for e in job.pending()} == {imports.MATCHED}

    # Asked twice, it does not keep re-reporting the same rows.
    assert job.forget_downloads_that_are_gone() == 0


# ── A refused search is not a missing song ────────────────────────

def test_a_search_that_never_answered_is_not_recorded_as_missing():
    """The failure that turned 6 missing songs into 132.

    YouTube Music does not answer a run of searches reliably — it resets
    connections and returns bodies that are not JSON. That was being swallowed
    into an empty result list, and an empty result list is exactly what a song
    that does not exist looks like. The songs were all there; nobody ever
    managed to ask about them.
    """
    from rose_bouquet.core.spotify import ImportReport, match_all
    from rose_bouquet.core.ytmusic import SearchUnavailable

    tracks = [
        spotify.SpotifyTrack(title="Found", artist="A", album=""),
        spotify.SpotifyTrack(title="Genuinely absent", artist="B", album=""),
        spotify.SpotifyTrack(title="Never asked", artist="C", album=""),
    ]

    def finder(title, _artist):
        if title == "Found":
            return SimpleNamespace(id="vid")
        if title == "Never asked":
            raise SearchUnavailable("Connection reset by peer")
        return None

    report = match_all(tracks, finder, workers=1)

    assert [t.title for t, _ in report.matched] == ["Found"]
    assert [t.title for t in report.missed] == ["Genuinely absent"]
    assert [t.title for t in report.unreachable] == ["Never asked"]
    # All three are still accounted for.
    assert report.total == 3
    assert "1 missing" in report.summary and "1 not searched yet" in report.summary

    # And the one nobody could ask about is not written into the playlist as a
    # song that does not exist.
    missing_text = " ".join(ImportReport.missed_lines(report))
    assert "Genuinely absent" in missing_text
    assert "Never asked" not in missing_text


def test_a_search_is_asked_again_before_it_counts_as_unanswerable(monkeypatch):
    from rose_bouquet.core import ytmusic

    calls = []

    class FlakyApi:
        def search(self, query, filter=None, limit=25):
            calls.append(query)
            if len(calls) < 3:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            return [{"videoId": "v", "title": "A song", "artists": [{"name": "A"}]}]

    music = ytmusic.YouTubeMusic.__new__(ytmusic.YouTubeMusic)
    music._api, music._failed = FlakyApi(), False
    monkeypatch.setattr(ytmusic.time, "sleep", lambda _s: None)

    found = music.best_match("A song", "A")
    assert found is not None
    assert len(calls) == 3          # two refusals, then an answer


def test_a_search_that_never_works_says_so_rather_than_finding_nothing(monkeypatch):
    from rose_bouquet.core import ytmusic

    class DeadApi:
        def search(self, query, filter=None, limit=25):
            raise ConnectionResetError(104, "Connection reset by peer")

    music = ytmusic.YouTubeMusic.__new__(ytmusic.YouTubeMusic)
    music._api, music._failed = DeadApi(), False
    monkeypatch.setattr(ytmusic.time, "sleep", lambda _s: None)

    with pytest.raises(ytmusic.SearchUnavailable):
        music.best_match("A song", "A")

    # The search box still gets an empty list, because there is nothing useful
    # to show someone typing either way.
    assert music.search("A song") == []
