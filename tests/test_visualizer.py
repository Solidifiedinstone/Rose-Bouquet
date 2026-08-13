"""The visualiser's shapes, intensity and frame rate.

Painting needs a QApplication and a screen, so what is tested here is the part
that decides *what* to draw rather than the drawing itself.
"""

from __future__ import annotations

from rose_bouquet.core import cava
from rose_bouquet.ui.visualizer import MAX_INTENSITY, MIN_INTENSITY, Shape


def test_every_shape_has_a_label():
    """The settings picker is built by iterating the enum."""
    labels = [shape.label for shape in Shape]
    assert len(labels) == len(set(labels))
    assert all(labels)


def test_shapes_made_of_hard_edges_are_not_blurred():
    """A blur turns bars and dots to mush, so it is ignored for them."""
    assert not Shape.BARS.blurs_well
    assert not Shape.BLOCKS.blurs_well
    assert not Shape.DOTS.blurs_well

    assert Shape.WAVE.blurs_well
    assert Shape.RADIAL.blurs_well


def test_a_saved_shape_that_no_longer_exists_does_not_crash():
    """Preferences may name a shape from a version that had a different set."""
    from rose_bouquet.ui.preferences import Preferences

    prefs = Preferences(visualizer_shape="an-old-shape")
    assert prefs.shape() is Shape.WAVE


def test_intensity_bounds_are_sane():
    assert MIN_INTENSITY > 0
    assert MIN_INTENSITY < 1.0 < MAX_INTENSITY


def test_the_frame_rate_bounds_allow_the_usual_choices():
    for rate in (24, 30, 60, 120, 144):
        assert cava.clamp_framerate(rate) == rate


# ── Icons ─────────────────────────────────────────────────────────

def test_every_result_kind_has_its_own_drawing():
    """'A' meant both album and artist, which is the reason these exist."""
    from rose_bouquet.ui.icons import _DRAWERS

    for kind in ("song", "video", "album", "playlist", "artist"):
        assert kind in _DRAWERS

    assert _DRAWERS["album"] is not _DRAWERS["artist"]
    assert _DRAWERS["song"] is not _DRAWERS["video"]


def test_every_icon_is_named_for_a_tooltip():
    """An icon nobody can name is a letter with extra steps."""
    from rose_bouquet.ui.icons import _DRAWERS, LABELS

    assert set(_DRAWERS) <= set(LABELS)


# ── Colour ────────────────────────────────────────────────────────

def test_rainbow_gives_every_band_its_own_colour():
    from rose_bouquet.ui.visualizer import ColourMode, Palette

    palette = Palette(mode=ColourMode.RAINBOW)
    colours = {palette.colour(i, 12).name() for i in range(12)}
    assert len(colours) > 8


def test_several_colours_blend_rather_than_step():
    """Stepping makes a wave look like a stack of coloured bricks.

    Bands that land exactly on a chosen colour are that colour; it is the ones
    between them that have to be mixed.
    """
    from rose_bouquet.ui.visualizer import ColourMode, Palette

    palette = Palette(mode=ColourMode.MULTI, colours=["#ff0000", "#0000ff"])
    shades = [palette.colour(i, 5) for i in range(5)]

    mixed = [c for c in shades if c.red() not in (0, 255) and c.blue() not in (0, 255)]
    assert mixed, [c.name() for c in shades]


def test_one_colour_is_one_colour():
    from rose_bouquet.ui.visualizer import ColourMode, Palette

    palette = Palette(mode=ColourMode.SOLID, colours=["#00ff00"])
    assert {palette.colour(i, 6).name() for i in range(6)} == {"#00ff00"}


def test_the_theme_accent_is_the_default():
    from rose_bouquet.ui.visualizer import Palette

    assert Palette("#123456").colour(0, 4).name() == "#123456"


def test_a_still_palette_does_not_drift():
    from rose_bouquet.ui.visualizer import ColourMotion, Palette

    palette = Palette(motion=ColourMotion.STATIC)
    before = palette.phase
    palette.advance(1.0, 0.8)
    assert palette.phase == before


def test_moving_palettes_move():
    from rose_bouquet.ui.visualizer import ColourMotion, Palette

    for motion in (ColourMotion.FADE, ColourMotion.SWEEP, ColourMotion.PULSE):
        palette = Palette(motion=motion)
        palette.advance(1.0, 0.8)
        assert palette.phase > 0, motion


def test_flash_tracks_loudness_not_the_clock():
    from rose_bouquet.ui.visualizer import ColourMotion, Palette

    palette = Palette("#808080", motion=ColourMotion.FLASH)
    quiet = palette.colour(0, 4, value=0.05)
    loud = palette.colour(0, 4, value=1.0)
    assert loud.valueF() > quiet.valueF()


def test_the_single_colour_fast_path_is_only_for_single_colours():
    from rose_bouquet.ui.visualizer import ColourMode, ColourMotion, Palette

    assert Palette().single
    assert not Palette(mode=ColourMode.RAINBOW).single
    assert not Palette(motion=ColourMotion.FADE).single


def test_shapes_that_move_by_themselves_are_marked():
    """These animate through a held note, so their frames cannot be skipped."""
    assert Shape.TURNTABLE.animated
    assert Shape.TUNNEL.animated
    assert not Shape.BARS.animated


def test_only_the_turntable_wants_the_sleeve():
    assert Shape.TURNTABLE.wants_artwork
    assert not Shape.WAVE.wants_artwork


# ── Layers ────────────────────────────────────────────────────────

def test_a_stack_of_shapes_is_read_from_preferences():
    from rose_bouquet.ui.preferences import Preferences

    prefs = Preferences(visualizer_layers=["turntable", "radial-bars"])
    assert [s.label for s in prefs.layers()] == ["Turntable", "Radial bars"]


def test_an_empty_stack_falls_back_to_the_single_shape():
    """Nothing ticked is far more likely to be a mistake than a request for a
    blank rectangle."""
    from rose_bouquet.ui.preferences import Preferences

    prefs = Preferences(visualizer_layers=[], visualizer_shape="dots")
    assert prefs.layers() == [Shape.DOTS]


def test_a_shape_that_no_longer_exists_is_dropped_not_fatal():
    """A preferences file from a newer version must not stop an older one."""
    from rose_bouquet.ui.preferences import Preferences

    prefs = Preferences(visualizer_layers=["wave", "shape-from-the-future"])
    assert prefs.layers() == [Shape.WAVE]


def test_a_stack_of_nothing_but_nonsense_still_draws_something():
    from rose_bouquet.ui.preferences import Preferences

    prefs = Preferences(visualizer_layers=["nope"], visualizer_shape="also-nope")
    assert prefs.layers() == [Shape.WAVE]


def test_the_full_screen_stack_is_its_own():
    from rose_bouquet.ui.preferences import Preferences

    prefs = Preferences(
        visualizer_layers=["bars"],
        visualizer_fullscreen_layers=["tunnel", "starfield"],
    )
    assert prefs.layers() == [Shape.BARS]
    assert prefs.layers(fullscreen=True) == [Shape.TUNNEL, Shape.STARFIELD]


# ── Hanging variants ──────────────────────────────────────────────

def test_hanging_shapes_are_drawn_by_their_upright_twin():
    """One implementation of bars, two directions — not two that drift apart."""
    assert Shape.BARS_TOP.hanging
    assert Shape.BARS_TOP.upright is Shape.BARS
    assert Shape.WAVE_TOP.upright is Shape.WAVE


def test_an_upright_shape_is_its_own_upright():
    assert not Shape.BARS.hanging
    assert Shape.BARS.upright is Shape.BARS


def test_a_hanging_shape_blurs_like_the_one_it_copies():
    assert Shape.WAVE_TOP.blurs_well == Shape.WAVE.blurs_well
    assert Shape.BARS_TOP.blurs_well == Shape.BARS.blurs_well


def test_every_hanging_shape_has_an_upright_to_draw_it():
    for shape in Shape:
        if shape.hanging:
            assert shape.upright is not shape


# ── Scale ─────────────────────────────────────────────────────────

def test_a_shape_with_no_scale_set_is_natural_size():
    from rose_bouquet.ui.visualizer import MAX_SCALE, MIN_SCALE

    assert MIN_SCALE < 100 < MAX_SCALE


def test_scales_are_stored_per_shape():
    from rose_bouquet.ui.preferences import Preferences

    prefs = Preferences(visualizer_scales={"radial-bars": 180, "bars": 60})
    assert prefs.visualizer_scales["radial-bars"] == 180
    assert prefs.visualizer_scales["bars"] == 60


def test_a_scale_from_a_broken_file_does_not_stop_the_app(tmp_path):
    import json

    from rose_bouquet.ui.preferences import Preferences

    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({"visualizer_scales": {"bars": "enormous", "wave": 150}}))

    prefs = Preferences.load(path)
    assert prefs.visualizer_scales == {"wave": 150}
