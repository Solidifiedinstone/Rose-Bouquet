"""Cover art resolution, and the MPRIS mapping that consumes it."""

from __future__ import annotations

from pathlib import Path

import pytest

from rose_bouquet.core import artwork, mpris
from rose_bouquet.core.library import Track
from rose_bouquet.core.playqueue import Repeat


@pytest.fixture(autouse=True)
def covers_in_tmp(tmp_path, monkeypatch):
    """Keep the cover cache out of the real data directory."""
    monkeypatch.setattr(artwork, "data_dir", lambda: tmp_path)
    return tmp_path


def test_sidecar_cover_wins(tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"not really a jpeg")
    track = Track(path=str(tmp_path / "song.mp3"), cover=str(cover))

    assert artwork.local_art(track) == str(cover)
    assert artwork.art_url(track) == cover.as_uri()


def test_sidecar_that_has_been_deleted_is_ignored(tmp_path):
    """A path saved in the library last week may not be there today."""
    track = Track(path=str(tmp_path / "song.mp3"), cover=str(tmp_path / "gone.jpg"))
    assert artwork.local_art(track) == ""


def test_youtube_track_falls_back_to_the_thumbnail():
    track = Track(path="/nowhere/song.mp3", source="youtube", source_id="dQw4w9WgXcQ")
    assert artwork.art_url(track) == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


def test_local_art_beats_the_thumbnail(tmp_path):
    """A downloaded track has its art embedded; that is the real cover."""
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png-ish")
    track = Track(path=str(tmp_path / "song.mp3"), cover=str(cover),
                  source="youtube", source_id="abc123")
    assert artwork.art_url(track).startswith("file://")


def test_a_local_track_with_no_art_gets_no_url(tmp_path):
    track = Track(path=str(tmp_path / "song.mp3"))
    assert artwork.art_url(track) == ""


def test_no_track_at_all():
    assert artwork.art_url(None) == ""


def test_missing_file_does_not_raise():
    assert artwork.embedded_art("/definitely/not/here.mp3") == ""


def test_a_file_with_no_picture_is_only_parsed_once(tmp_path, covers_in_tmp):
    """The miss is remembered, so track changes do not re-parse the file."""
    song = tmp_path / "song.mp3"
    song.write_bytes(b"not an mp3 at all")

    assert artwork.embedded_art(str(song)) == ""
    misses = list((covers_in_tmp / "covers").glob("*.none"))
    assert len(misses) == 1


def test_retagging_a_file_invalidates_its_cached_cover(tmp_path, covers_in_tmp):
    """The cache key includes the modification time, so a retag is not stale."""
    song = tmp_path / "song.mp3"
    song.write_bytes(b"one")
    artwork.embedded_art(str(song))

    import os

    os.utime(song, (0, 0))
    artwork.embedded_art(str(song))

    assert len(list((covers_in_tmp / "covers").glob("*.none"))) == 2


def test_loop_status_maps_both_ways():
    for repeat, status in mpris._REPEAT_TO_LOOP.items():
        assert mpris._LOOP_TO_REPEAT[status] is repeat

    assert mpris._REPEAT_TO_LOOP[Repeat.OFF] == "None"
    assert mpris._REPEAT_TO_LOOP[Repeat.ALL] == "Playlist"
    assert mpris._REPEAT_TO_LOOP[Repeat.ONE] == "Track"


def test_the_bus_name_is_one_a_controller_will_find():
    """Controllers enumerate names under this prefix; anything else is invisible."""
    assert mpris.BUS_NAME.startswith("org.mpris.MediaPlayer2.")
    assert mpris.OBJECT_PATH == "/org/mpris/MediaPlayer2"
