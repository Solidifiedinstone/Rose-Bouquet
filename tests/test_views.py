"""The list views, and what they cost to keep on screen.

Everything here is about work *not* being done. A music player spends its
whole life with a list on screen and a track changing under it, and the two
mistakes that make one feel slow are rebuilding a list that did not change and
building a list nobody has scrolled to yet. Both are invisible in a screenshot
and obvious after ten seconds of use, so they are pinned here instead.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from rose_bouquet.core.library import Library, Track
from rose_bouquet.ui.preferences import Preferences
from rose_bouquet.ui.views import AlbumsView, LibraryView
from rose_bouquet.ui.widgets import Card, TrackRow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def appearance():
    return Preferences().appearance()


def _library(count: int) -> Library:
    library = Library()
    for index in range(count):
        library.add(Track(
            path=f"/music/{index:04d}.mp3",
            title=f"Track {index}",
            artist=f"Artist {index // 12}",
            album=f"Album {index // 12}",
            duration=180,
            track_number=index % 12,
        ))
    return library


# ── A track change is not a redraw ────────────────────────────────

def test_the_playing_highlight_moves_without_rebuilding_the_list(app, appearance):
    view = LibraryView(_library(300), appearance)
    view.refresh("")
    while view._more_to_build():
        view._extend()

    rows = view.body.findChildren(TrackRow)
    assert len(rows) == 300
    identities = [id(row) for row in rows]

    view.set_playing("/music/0007.mp3")

    # The same row widgets, still there: nothing was thrown away and made
    # again, so the scroll position and everything below it survived.
    assert [id(row) for row in view.body.findChildren(TrackRow)] == identities
    assert [row.track.path for row in rows if row.playing] == ["/music/0007.mp3"]

    view.set_playing("/music/0200.mp3")
    assert [row.track.path for row in rows if row.playing] == ["/music/0200.mp3"]

    # And stopping clears it rather than leaving the last track lit.
    view.set_playing("")
    assert not any(row.playing for row in rows)


def test_a_redraw_that_was_not_told_what_is_playing_keeps_the_highlight(app, appearance):
    view = LibraryView(_library(20), appearance)
    view.set_playing("/music/0003.mp3")
    view.refresh()

    lit = [row.track.path for row in view.body.findChildren(TrackRow) if row.playing]
    assert lit == ["/music/0003.mp3"]


# ── Long lists arrive in blocks ───────────────────────────────────

def test_a_thousand_tracks_do_not_all_become_widgets_at_once(app, appearance):
    view = LibraryView(_library(1000), appearance)
    view.refresh("")

    assert len(view._tracks) == 1000
    assert view._built <= 300          # a block or two, not the library
    assert view._more_to_build()

    while view._more_to_build():
        view._extend()
    assert view._built == 1000         # scrolling to the end still gets you there


def test_an_album_wall_is_built_a_few_rows_at_a_time(app, appearance):
    view = AlbumsView(_library(1200), appearance)   # 100 albums of 12
    view.refresh("")

    assert len(view._albums) == 100
    assert view._built <= AlbumsView.CHUNK
    assert 0 < len(view.body.findChildren(Card)) <= AlbumsView.CHUNK

    while view._more_to_build():
        view._extend()
    assert len(view.body.findChildren(Card)) == 100


def test_opening_one_album_leaves_no_half_built_wall_behind(app, appearance):
    view = AlbumsView(_library(1200), appearance)
    view.refresh("")
    view.show_album(("Artist 0", "Album 0"))

    # The wall is gone, so a stray scroll must not try to extend it into a
    # layout that was deleted underneath it.
    assert not view._more_to_build()
    view._maybe_extend(0)


# ── The sections that are not built until they are opened ─────────

def test_the_expensive_sections_are_not_built_until_they_are_opened(app):
    from rose_bouquet.ui.main_window import Sections

    adopted: list = []
    built: list = []
    views = Sections(adopted.append)

    eager = QWidget()
    views.add("library", eager)

    def factory():
        built.append("watch")
        return QLabel("a browser engine, pretend")

    views.add_lazy("watch", factory)

    # Registering costs nothing, and restyling every built section must not
    # quietly build the one section we are trying not to build.
    assert built == []
    assert list(views.values()) == [eager]
    assert "watch" in views
    assert views.built("watch") is None

    watch = views["watch"]
    assert built == ["watch"]
    assert watch in adopted

    # Asked for twice, built once.
    assert views["watch"] is watch
    assert views.get("watch") is watch
    assert built == ["watch"]
    assert views.built("watch") is watch

    # And a section nobody registered is still simply absent.
    assert views.get("nonsense") is None
    with pytest.raises(KeyError):
        views["nonsense"]


# ── A download is refused only for music you actually have ────────

def test_a_download_is_not_refused_because_of_a_file_that_is_gone(tmp_path):
    """The failure that made a nine-hundred-track import fetch about twenty.

    Every request was checked against the library, and the library still
    listed every track, pointing at files that had been deleted. So nearly
    every download was declined as one you already had, and the only ones that
    got through were the handful that happened not to be listed.
    """
    from rose_bouquet.ui.main_window import MainWindow

    here = tmp_path / "here.mp3"
    here.write_bytes(b"audio")

    # The real method, on an object with only what it reads — building a whole
    # window would test Qt rather than this.
    window = MainWindow.__new__(MainWindow)
    window.library = Library()
    window.library.add(Track(path=str(here), title="Here", artist="A",
                             source="youtube", source_id="have"))
    window.library.add(Track(path=str(tmp_path / "gone.mp3"), title="Gone",
                             artist="A", source="youtube", source_id="lost"))

    assert window.already_have("have")
    assert not window.already_have("lost")        # the file went; fetch it again
    assert not window.already_have("never-seen")
    assert not window.already_have("")
