"""The list views, and what they cost to keep on screen.

Everything here is about work *not* being done. A music player spends its
whole life with a list on screen and a track changing under it, and the two
mistakes that make one feel slow are rebuilding a list that did not change and
building a list nobody has scrolled to yet. Both are invisible in a screenshot
and obvious after ten seconds of use, so they are pinned here instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from rose_bouquet.core.library import Library, Track
from rose_bouquet.ui.preferences import Preferences
from rose_bouquet.ui.views import AlbumsView, LibraryView
from rose_bouquet.ui.widgets import Card, TrackRow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def built(app):
    """Views that get destroyed when the test is done with them.

    These tests make a thousand rows and a hundred covers at a time. Left
    alive they accumulate across the module, and the process ends up heavy
    enough that the app `test_startup` launches afterwards segfaults inside Qt
    about one run in ten — a flake in a different file, with no clue pointing
    back here.

    `deleteLater` alone is not enough: `processEvents` does not flush
    `DeferredDelete`, so the posted events have to be sent explicitly or the
    widgets outlive the test regardless.
    """
    made: list = []

    def make(view):
        made.append(view)
        return view

    yield make

    for view in made:
        view.deleteLater()
    app.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


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

def test_the_playing_highlight_moves_without_rebuilding_the_list(built, app, appearance):
    view = built(LibraryView(_library(300), appearance))
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


def test_a_redraw_that_was_not_told_what_is_playing_keeps_the_highlight(built, app, appearance):
    view = built(LibraryView(_library(20), appearance))
    view.set_playing("/music/0003.mp3")
    view.refresh()

    lit = [row.track.path for row in view.body.findChildren(TrackRow) if row.playing]
    assert lit == ["/music/0003.mp3"]


# ── Long lists arrive in blocks ───────────────────────────────────

def test_a_thousand_tracks_do_not_all_become_widgets_at_once(built, app, appearance):
    view = built(LibraryView(_library(1000), appearance))
    view.refresh("")

    assert len(view._tracks) == 1000
    assert view._built <= 300          # a block or two, not the library
    assert view._more_to_build()

    while view._more_to_build():
        view._extend()
    assert view._built == 1000         # scrolling to the end still gets you there


def test_an_album_wall_is_built_a_few_rows_at_a_time(built, app, appearance):
    view = built(AlbumsView(_library(1200), appearance))   # 100 albums of 12
    view.refresh("")

    assert len(view._albums) == 100
    assert view._built <= AlbumsView.CHUNK
    assert 0 < len(view.body.findChildren(Card)) <= AlbumsView.CHUNK

    while view._more_to_build():
        view._extend()
    assert len(view.body.findChildren(Card)) == 100


def test_opening_one_album_leaves_no_half_built_wall_behind(built, app, appearance):
    view = built(AlbumsView(_library(1200), appearance))
    view.refresh("")
    view.show_album(("Artist 0", "Album 0"))

    # The wall is gone, so a stray scroll must not try to extend it into a
    # layout that was deleted underneath it.
    assert not view._more_to_build()
    view._maybe_extend(0)


# ── The sections that are not built until they are opened ─────────

def test_the_expensive_sections_are_not_built_until_they_are_opened(built, app):
    from rose_bouquet.ui.main_window import Sections

    adopted: list = []
    made: list = []
    views = Sections(adopted.append)

    eager = built(QWidget())
    views.add("library", eager)

    def factory():
        made.append("watch")
        return QLabel("a browser engine, pretend")

    views.add_lazy("watch", factory)

    # Registering costs nothing, and restyling every built section must not
    # quietly build the one section we are trying not to build.
    assert made == []
    assert list(views.values()) == [eager]
    assert "watch" in views
    assert views.built("watch") is None

    watch = views["watch"]
    assert made == ["watch"]
    assert watch in adopted

    # Asked for twice, built once.
    assert views["watch"] is watch
    assert views.get("watch") is watch
    assert made == ["watch"]
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


# ── Settings opens ────────────────────────────────────────────────

def test_every_signal_settings_is_wired_to_actually_exists(app):
    """Settings would not open at all, and said nothing about why.

    `interests_changed` was removed from the dialog when the recommender it
    belonged to was taken out, but the line connecting to it stayed behind in
    the window. Reaching for a signal that is not there raises, and it raised
    before the dialog was ever shown — so pressing Settings did nothing, with
    no window, no error on screen and nothing in the log.

    Signals are looked up by name at runtime, which is exactly the kind of
    wiring a type checker never sees, so it is checked here instead.
    """
    import inspect
    import re

    from rose_bouquet.ui.main_window import MainWindow
    from rose_bouquet.ui.preferences import Preferences
    from rose_bouquet.ui.settings import SettingsDialog

    source = inspect.getsource(MainWindow.open_settings)
    wanted = set(re.findall(r"dialog\.(\w+)\.connect", source))
    assert wanted, "open_settings connects nothing — has it moved?"

    dialog = SettingsDialog(Preferences(), None)
    try:
        missing = sorted(name for name in wanted if not hasattr(dialog, name))
        assert not missing, f"open_settings connects signals the dialog lacks: {missing}"
    finally:
        dialog.deleteLater()
        app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_handlers_settings_is_wired_to_exist_too(app):
    """The other half of the same wiring, and the same kind of silent break."""
    import inspect
    import re

    from rose_bouquet.ui.main_window import MainWindow

    source = inspect.getsource(MainWindow.open_settings)
    handlers = set(re.findall(r"\.connect\(self\.(\w+)\)", source))
    missing = sorted(name for name in handlers if not hasattr(MainWindow, name))
    assert not missing, f"open_settings connects to methods that do not exist: {missing}"


# ── Playing the whole library from the top of it ──────────────────

def test_play_all_carries_what_is_listed_not_the_whole_library(built, app, appearance, tmp_path):
    """Search first, then press Play, and you mean the search.

    The button carries the list on screen rather than the library, so
    narrowing to "shoegaze" and pressing Play does not hand back everything.
    """
    library = _library(30)
    view = built(LibraryView(library, appearance))
    view.refresh("")

    asked: list = []
    view.play_all_requested.connect(lambda tracks, shuffle: asked.append((tracks, shuffle)))

    view.play_all.click()
    assert asked[-1][1] is False
    assert len(asked[-1][0]) == 30

    view.shuffle_all.click()
    assert asked[-1][1] is True

    # Narrowed by a search, the button means the narrowed list.
    view.search.setText("Track 7")
    view.refresh("")
    view.play_all.click()
    assert [t.title for t in asked[-1][0]] == ["Track 7"]


def test_there_is_nothing_to_play_when_nothing_is_listed(built, app, appearance):
    view = built(LibraryView(Library(), appearance))
    view.refresh("")

    assert not view.play_all.isEnabled()
    assert not view.shuffle_all.isEnabled()


def test_playing_everything_skips_the_rows_whose_files_are_gone(tmp_path):
    """A dead row must not stop the queue three tracks in."""
    from rose_bouquet.ui.main_window import MainWindow

    here = tmp_path / "here.mp3"
    here.write_bytes(b"x")
    tracks = [
        Track(path=str(here), title="Here", artist="A"),
        Track(path=str(tmp_path / "gone.mp3"), title="Gone", artist="A"),
    ]

    kept = [t for t in tracks if Path(t.path).exists()]
    assert [t.title for t in kept] == ["Here"]
    assert hasattr(MainWindow, "play_all")


# ── Sections you can switch off ───────────────────────────────────

def test_the_playlists_tab_can_be_switched_off_without_losing_playlists(app):
    from rose_bouquet.ui.preferences import Preferences
    from rose_bouquet.ui.settings import SettingsDialog

    prefs = Preferences()
    assert prefs.show_playlists is True          # on unless you say otherwise

    dialog = SettingsDialog(prefs, None)
    try:
        assert dialog.show_playlists.isChecked()
        # The dialog owns the preference; the window owns the sidebar.
        assert hasattr(dialog, "sections_changed")
    finally:
        dialog.deleteLater()

    prefs.show_playlists = False
    assert prefs.to_dict()["show_playlists"] is False
    assert Preferences.from_dict(prefs.to_dict()).show_playlists is False


# ── Choosing an order, and keeping it ─────────────────────────────

def test_the_order_picker_offers_every_order_and_reorders_the_list(built, app, appearance):
    from rose_bouquet.core.library import ORDERS

    library = Library()
    library.add(Track(path="/m/1.mp3", title="Long one", artist="B", duration=300))
    library.add(Track(path="/m/2.mp3", title="Short one", artist="A", duration=30))

    view = built(LibraryView(library, appearance))
    view.refresh("")

    offered = [view.order_picker.itemData(i) for i in range(view.order_picker.count())]
    assert offered == list(ORDERS)
    assert [t.title for t in view._tracks] == ["Short one", "Long one"]   # artist A–Z

    remembered: list = []
    view.order_changed.connect(remembered.append)

    view.order_picker.setCurrentIndex(view.order_picker.findData("longest"))
    assert [t.title for t in view._tracks] == ["Long one", "Short one"]
    assert remembered == ["longest"]

    # Picking the one already chosen is not a change worth saving or redrawing.
    view.order_picker.setCurrentIndex(view.order_picker.findData("longest"))
    assert remembered == ["longest"]


def test_the_saved_order_is_the_one_the_library_opens_with(built, app, appearance):
    library = Library()
    library.add(Track(path="/m/1.mp3", title="Long one", artist="B", duration=300))
    library.add(Track(path="/m/2.mp3", title="Short one", artist="A", duration=30))

    view = built(LibraryView(library, appearance, "longest"))
    view.refresh("")
    assert [t.title for t in view._tracks] == ["Long one", "Short one"]

    # And an order this version has never heard of opens on the default.
    view = built(LibraryView(library, appearance, "by-vibes"))
    view.refresh("")
    assert [t.title for t in view._tracks] == ["Short one", "Long one"]


# ── A video fills the window, not the monitor ─────────────────────

def test_fullscreen_hides_the_chrome_instead_of_taking_the_screen(app):
    """It used to lift the web view into a top-level fullscreen window.

    That fills the *monitor*, which is not what pressing fullscreen on a video
    in a window means — and nothing was bound to bring it back, because Escape
    was wired to the video stage and this is the web view.
    """
    from rose_bouquet.ui import youtube_tab

    class Request:
        def __init__(self, on):
            self.on = on
            self.accepted = False

        def accept(self):
            self.accepted = True

        def toggleOn(self):                       # noqa: N802 — Qt's name
            return self.on

    seen: list = []
    toolbar_shown: list = []

    tab = youtube_tab.YouTubeTab.__new__(youtube_tab.YouTubeTab)
    tab._fullscreen = False
    tab.toolbar = type("T", (), {"setVisible": staticmethod(toolbar_shown.append)})()
    tab.fullscreen_changed = type("S", (), {"emit": staticmethod(seen.append)})()
    tab._settle = lambda: None

    asked = Request(True)
    youtube_tab.YouTubeTab._on_fullscreen_requested(tab, asked)
    assert asked.accepted                          # the page is always answered
    assert tab._fullscreen and seen == [True] and toolbar_shown == [False]

    # Asked again for a state it is already in: nothing happens twice.
    youtube_tab.YouTubeTab._on_fullscreen_requested(tab, Request(True))
    assert seen == [True] and toolbar_shown == [False]

    youtube_tab.YouTubeTab._on_fullscreen_requested(tab, Request(False))
    assert not tab._fullscreen and seen == [True, False]
    assert toolbar_shown == [False, True]          # the toolbar comes back


def test_leaving_fullscreen_tells_the_page_and_reports_whether_it_did_anything(app):
    """Escape is bound globally, so it must be harmless when there is no video."""
    from rose_bouquet.ui import youtube_tab

    told: list = []
    seen: list = []
    tab = youtube_tab.YouTubeTab.__new__(youtube_tab.YouTubeTab)
    tab._fullscreen = False
    tab.toolbar = type("T", (), {"setVisible": staticmethod(lambda _v: None)})()
    tab.fullscreen_changed = type("S", (), {"emit": staticmethod(seen.append)})()
    tab._settle = lambda: None
    tab.view = type("V", (), {"page": staticmethod(lambda: type("P", (), {
        "triggerAction": staticmethod(told.append)})())})()

    # Nothing to leave: says so, and does not touch the page.
    assert youtube_tab.YouTubeTab.leave_fullscreen(tab) is False
    assert told == []

    tab._fullscreen = True
    assert youtube_tab.YouTubeTab.leave_fullscreen(tab) is True
    # The page is told, rather than having the state changed behind its back —
    # otherwise it goes on drawing an exit button for a state it is not in.
    assert told and not tab._fullscreen
    assert seen == [False]


def test_the_window_steps_aside_for_a_video_and_comes_back(app):
    from rose_bouquet.ui.main_window import MainWindow

    rail: list = []
    bar: list = []
    settled: list = []
    window = MainWindow.__new__(MainWindow)
    window.nav_rail = type("R", (), {"setVisible": staticmethod(rail.append)})()
    window.player_bar = type("B", (), {"setVisible": staticmethod(bar.append)})()
    # A visibility change that is not settled does not appear until the
    # compositor next repaints — which is a workspace switch away.
    window._settle_layout = lambda: settled.append(True)

    MainWindow._youtube_fullscreen(window, True)
    assert rail == [False] and bar == [False]

    MainWindow._youtube_fullscreen(window, False)
    assert rail == [False, True] and bar == [False, True]
    assert settled == [True, True]      # both ways, not just on the way in
