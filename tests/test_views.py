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
from rose_bouquet.ui.views import LibraryView
from rose_bouquet.ui.widgets import TrackRow


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
    while view._built < len(view._tracks):
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
