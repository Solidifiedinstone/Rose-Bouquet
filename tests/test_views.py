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


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


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
