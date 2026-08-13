"""The visualiser — cava's output, drawn.

cava's levels become a filled curve: a moving-average smoothing pass with a
window of 2, filled at low alpha under the line, then blurred. Several desktop
bars draw their visualiser this way, so a player using the same source and the
same settings moves in step with one.

Several shapes are offered, because a visualiser is decoration and decoration
should be a preference: a wave, bars (classic cava), and a mirrored wave for
the full-window background behind the now-playing art.

The reader thread only ever *appends to a list*, and the widget only ever reads
the most recent frame. No locking, no queue: the worst a race can do here is
draw one frame that is half old, sixty times a second, which nobody can see.
"""

from __future__ import annotations

import logging
import math
import threading
from enum import Enum
from typing import Optional, Sequence

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsBlurEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core import cava
from rose_bouquet.ui.theme import Appearance

logger = logging.getLogger(__name__)


class Shape(str, Enum):
    WAVE = "wave"
    BARS = "bars"
    MIRROR = "mirror"
    LINE = "line"
    BLOCKS = "blocks"
    DOTS = "dots"
    RADIAL = "radial"
    RADIAL_BARS = "radial-bars"
    RADIAL_DOTS = "radial-dots"
    RADIAL_LINES = "radial-lines"
    RADIAL_MIRROR = "radial-mirror"
    RADIAL_BLOOM = "radial-bloom"
    TURNTABLE = "turntable"
    RIBBONS = "ribbons"
    TUNNEL = "tunnel"
    STARFIELD = "starfield"

    # Hanging from the top instead of standing on the floor. Drawn by the same
    # painters through a vertical flip, so there is one implementation of bars
    # rather than two that drift apart.
    BARS_TOP = "bars-top"
    BLOCKS_TOP = "blocks-top"
    DOTS_TOP = "dots-top"
    WAVE_TOP = "wave-top"
    LINE_TOP = "line-top"

    @property
    def label(self) -> str:
        return {
            Shape.WAVE: "Wave",
            Shape.BARS: "Bars",
            Shape.MIRROR: "Mirrored",
            Shape.LINE: "Line",
            Shape.BLOCKS: "Blocks",
            Shape.DOTS: "Dots",
            Shape.RADIAL: "Radial",
            Shape.RADIAL_BARS: "Radial bars",
            Shape.RADIAL_DOTS: "Radial dots",
            Shape.RADIAL_LINES: "Radial lines",
            Shape.RADIAL_MIRROR: "Radial mirrored",
            Shape.RADIAL_BLOOM: "Radial bloom",
            Shape.TURNTABLE: "Turntable",
            Shape.RIBBONS: "Ribbons",
            Shape.TUNNEL: "Tunnel",
            Shape.STARFIELD: "Starfield",
            Shape.BARS_TOP: "Bars, hanging",
            Shape.BLOCKS_TOP: "Blocks, hanging",
            Shape.DOTS_TOP: "Dots, hanging",
            Shape.WAVE_TOP: "Wave, hanging",
            Shape.LINE_TOP: "Line, hanging",
        }[self]

    @property
    def hanging(self) -> bool:
        """Whether it grows down from the top rather than up from the floor."""
        return self.value.endswith("-top")

    @property
    def upright(self) -> "Shape":
        """The shape this one is drawn from. Its own self, unless it hangs."""
        return Shape(self.value[:-4]) if self.hanging else self

    @property
    def animated(self) -> bool:
        """Whether the shape moves by itself, not only when the levels change.

        A record turns and a tunnel rushes outward even through a held note,
        so these cannot be skipped when the frame is unchanged.
        """
        return self in (Shape.TURNTABLE, Shape.RIBBONS, Shape.TUNNEL,
                        Shape.STARFIELD)

    @property
    def wants_artwork(self) -> bool:
        """Whether the shape draws the album art as part of itself."""
        return self is Shape.TURNTABLE

    @property
    def radial(self) -> bool:
        """Whether it is drawn around a ring rather than along a line.

        Radial shapes want a square-ish area and look best filling a screen;
        the straight ones are what fit a bar thirty pixels tall.
        """
        return self.value.startswith("radial") or self in (
            Shape.TURNTABLE, Shape.TUNNEL, Shape.STARFIELD)

    @property
    def blurs_well(self) -> bool:
        """Whether a blur flatters this shape.

        Anything made of deliberate hard edges — bars, blocks, dots — turns to
        mush under one, so the blur setting is honoured for the curves and
        quietly ignored for the rest rather than being allowed to ruin them.
        """
        return self.upright in (
            Shape.WAVE, Shape.MIRROR, Shape.LINE,
            Shape.RADIAL, Shape.RADIAL_LINES, Shape.RADIAL_MIRROR,
            Shape.RADIAL_BLOOM, Shape.RIBBONS, Shape.TUNNEL, Shape.STARFIELD,
        )


class ColourMode(str, Enum):
    """Where a visualiser's colours come from."""

    THEME = "theme"        # one colour, the theme's accent
    SOLID = "solid"        # one colour, chosen
    MULTI = "multi"        # a chosen set, spread across the bands
    RAINBOW = "rainbow"    # the whole spectrum, spread across the bands

    @property
    def label(self) -> str:
        return {
            ColourMode.THEME: "Theme accent",
            ColourMode.SOLID: "One colour",
            ColourMode.MULTI: "Several colours",
            ColourMode.RAINBOW: "Rainbow",
        }[self]


class ColourMotion(str, Enum):
    """What the colours do while the music plays."""

    STATIC = "static"      # nothing; band N is always the same colour
    FADE = "fade"          # the whole palette drifts round, slowly
    SWEEP = "sweep"        # a band of colour travels across the spectrum
    FLASH = "flash"        # brightness tracks how loud each band is
    PULSE = "pulse"        # the palette jumps on the beat

    @property
    def label(self) -> str:
        return {
            ColourMotion.STATIC: "Still",
            ColourMotion.FADE: "Fade",
            ColourMotion.SWEEP: "Sweep",
            ColourMotion.FLASH: "Flash",
            ColourMotion.PULSE: "Pulse",
        }[self]


#: How many colours a "several colours" palette may hold. Two is the fewest
#: that is not just "one colour", and past about eight neighbouring bands stop
#: being tellable apart on a thirty-pixel bar.
MIN_COLOURS = 2
MAX_COLOURS = 8

#: A pleasant default set, in case somebody turns multicolour on before
#: choosing anything.
DEFAULT_COLOURS = ("#e0607e", "#f6c177", "#9ccfd8", "#c4a7e7")


class Palette:
    """Decides what colour a band is drawn in.

    Kept apart from the shapes on purpose: every shape asks this the same
    question — "what colour is band N, which is this loud, at this moment" —
    so a new colour mode works in all twelve shapes without touching any of
    them, and a new shape gets every colour mode for free.
    """

    def __init__(self, accent: str = "#e0607e", *,
                 mode: ColourMode = ColourMode.THEME,
                 motion: ColourMotion = ColourMotion.STATIC,
                 colours: Optional[Sequence[str]] = None) -> None:
        self.accent = accent
        self.mode = mode
        self.motion = motion
        self.colours = [QColor(c) for c in (colours or DEFAULT_COLOURS)] or [QColor(accent)]
        #: Advances with time so motion has something to move along.
        self.phase = 0.0

    def advance(self, seconds: float, loudness: float) -> None:
        """Move the palette on by one frame's worth of time."""
        if self.motion is ColourMotion.FADE:
            self.phase += seconds * 0.08
        elif self.motion is ColourMotion.SWEEP:
            self.phase += seconds * 0.35
        elif self.motion is ColourMotion.PULSE:
            # Jumps with the music rather than with the clock, so it lands on
            # the beat instead of near it.
            self.phase += seconds * (0.1 + loudness * 2.2)
        self.phase %= 1000.0

    def colour(self, index: int, count: int, value: float = 1.0) -> QColor:
        """The colour for one band."""
        position = (index / max(1, count - 1)) if count > 1 else 0.0

        if self.motion in (ColourMotion.FADE, ColourMotion.SWEEP, ColourMotion.PULSE):
            position = (position + self.phase) % 1.0

        colour = self._base(position)

        if self.motion is ColourMotion.FLASH:
            # Loud bands burn, quiet ones sit back. Value is already 0–1, so
            # this is a straight read of the music rather than a timer.
            colour = QColor(colour)
            colour.setHsvF(colour.hueF(),
                           max(0.0, min(1.0, colour.saturationF() * (1.3 - value * 0.5))),
                           max(0.0, min(1.0, 0.45 + value * 0.55)))
        return colour

    def _base(self, position: float) -> QColor:
        if self.mode is ColourMode.RAINBOW:
            return QColor.fromHsvF(position % 1.0, 0.72, 1.0)

        if self.mode is ColourMode.MULTI and len(self.colours) > 1:
            # Blended between neighbours rather than stepped, so a wave does
            # not look like a stack of coloured bricks.
            span = position * len(self.colours)
            first = self.colours[int(span) % len(self.colours)]
            second = self.colours[(int(span) + 1) % len(self.colours)]
            return _blend(first, second, span - int(span))

        if self.mode is ColourMode.SOLID and self.colours:
            return self.colours[0]

        return QColor(self.accent)

    @property
    def single(self) -> bool:
        """Whether every band is the same colour, which lets a shape draw its
        fill in one pass instead of one per band."""
        return (self.mode in (ColourMode.THEME, ColourMode.SOLID)
                and self.motion in (ColourMotion.STATIC, ColourMotion.FLASH))


def _blend(first: QColor, second: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, amount))
    return QColor(
        round(first.red() + (second.red() - first.red()) * amount),
        round(first.green() + (second.green() - first.green()) * amount),
        round(first.blue() + (second.blue() - first.blue()) * amount),
    )


#: Below this, a frame is close enough to the last one that redrawing it would
#: not change a pixel. Generous on purpose: the eased fall-off approaches zero
#: asymptotically and would otherwise never quite stop repainting.
SETTLED = 0.002

#: A blur is an offscreen render of the whole widget, so its cost grows with
#: area — measured at roughly 5x the paint cost of a full-screen radial. Above
#: this many pixels the blur is dropped: at that size the shape is large enough
#: to read on its own, and a visualiser that drops frames looks far worse than
#: one without a glow.
BLUR_AREA_LIMIT = 1_200_000

#: How hard the visualiser reacts to sound, as a multiplier on cava's levels.
#: 1.0 is "as measured"; below that is calmer, above it exaggerates quiet
#: passages. Clamped rather than unbounded — past about 4x everything is
#: permanently at the ceiling and the visualiser stops meaning anything.
MIN_INTENSITY = 0.2
MAX_INTENSITY = 4.0

#: How large a shape is drawn, as a percentage of its natural size. Per shape,
#: because "bigger" means different things: a radial figure grows outward from
#: the middle and a bar grows up from the floor, and a size that suits one is
#: usually wrong for the other.
MIN_SCALE = 25
MAX_SCALE = 250


class CavaReader:
    """Runs cava and keeps the newest frame. Shared by every visualiser widget.

    One process, however many widgets: the player bar and the full-screen view
    both draw the same numbers, and starting a second cava would double the
    CPU cost to show the same thing twice.
    """

    _instance: Optional["CavaReader"] = None

    def __init__(self) -> None:
        self.frame: list[float] = [0.0] * cava.BARS
        self.running = False
        self.framerate = cava.FRAMERATE
        self._process = None
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def shared(cls) -> "CavaReader":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def available(self) -> bool:
        return cava.available()

    def start(self, framerate: Optional[int] = None) -> bool:
        if framerate is not None:
            framerate = cava.clamp_framerate(framerate)
            # cava's rate is baked into its config file at launch, so a change
            # means a restart. Only when it actually changed, though: a settings
            # dialog emits on every keystroke and restarting cava per keystroke
            # would make the visualiser stutter for as long as you are typing.
            if self.running and framerate != self.framerate:
                self.stop()
            self.framerate = framerate

        if self.running:
            return True

        self._process = cava.start(framerate=self.framerate)
        if self._process is None:
            return False

        self.running = True
        self._thread = threading.Thread(target=self._read, name="rose-bouquet-cava", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self.running = False
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:                     # noqa: BLE001 — it is going away
                pass
            self._process = None
        self.frame = [0.0] * cava.BARS

    def _read(self) -> None:
        stream = self._process.stdout if self._process else None
        if stream is None:
            self.running = False
            return

        try:
            for line in stream:
                if not self.running:
                    break
                if line.strip():
                    self.frame = cava.parse_frame(line)
        except (OSError, ValueError) as exc:
            logger.debug("cava reader stopped: %s", exc)
        finally:
            self.running = False


class Visualizer(QWidget):
    """A cava-driven visualiser, in the shape of your choosing."""

    clicked = Signal()

    def __init__(
        self,
        appearance: Appearance,
        *,
        shape: Shape = Shape.WAVE,
        height: int = 46,
        blur: bool = True,
        alpha: float = 0.15,
        intensity: float = 1.0,
        fps: int = 60,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.appearance = appearance
        self.shape = shape
        #: Drawn back to front. One shape is simply a stack of one, so there is
        #: no separate code path for the common case.
        self.layers: list[Shape] = [shape]
        self.alpha = alpha
        self.intensity = intensity
        self.live = False
        self._blur_wanted = blur
        self._blur_applied = False

        #: Colour is a whole subsystem of its own — see `Palette`.
        self.palette = Palette(appearance.theme.accent)

        #: Degrees. Drives the record, the tunnel and the ribbons alike, and
        #: only advances while sound is playing so a paused visualiser is
        #: genuinely still rather than idling.
        self._spin = 0.0
        #: The sleeve art, for the shapes that draw it.
        self._artwork: Optional[QPixmap] = None

        #: Per-shape size, keyed by shape value, as a fraction of natural size.
        self.scales: dict[str, float] = {}

        self.reader = CavaReader.shared()
        self.points: list[float] = [0.0] * cava.BARS
        #: The last drawn frame, eased towards the new one so the fall-off is
        #: smooth even when cava sends a hard zero.
        self.drawn: list[float] = [0.0] * cava.BARS

        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        self._apply_blur()

        self.fps = cava.clamp_framerate(fps)
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, 1000 // self.fps))
        self._timer.timeout.connect(self._pull)

    # ── Running ───────────────────────────────────────────────────

    def start(self) -> bool:
        started = self.reader.start(self.fps)
        self._timer.start()
        return started

    def stop(self) -> None:
        self._timer.stop()

    def set_live(self, live: bool) -> None:
        """Whether audio is playing. When it is not, the wave settles to flat."""
        self.live = live

    def set_layers(self, shapes: Sequence[Shape]) -> None:
        """Draw several shapes on top of each other.

        Empty falls back to the single shape rather than drawing nothing —
        an empty visualiser looks broken, and "none ticked" is far more likely
        to be a mistake than a request for a blank rectangle.
        """
        layers = [s for s in shapes if isinstance(s, Shape)]
        self.layers = layers or [self.shape]
        self.shape = self.layers[0]
        self._apply_blur()
        self.update()

    def set_shape(self, shape: Shape) -> None:
        self.shape = shape
        self.layers = [shape]
        # The blur has to be reconsidered: switching from a wave to bars with
        # the old effect still attached leaves a smear.
        self._apply_blur()
        self.update()

    def set_blur(self, blur: bool) -> None:
        self._blur_wanted = blur
        self._apply_blur()
        self.update()

    def set_intensity(self, intensity: float) -> None:
        self.intensity = max(MIN_INTENSITY, min(MAX_INTENSITY, float(intensity)))

    def set_fps(self, fps: int) -> None:
        """Redraw rate, and cava's sample rate with it."""
        self.fps = cava.clamp_framerate(fps)
        self._timer.setInterval(max(1, 1000 // self.fps))
        if self._timer.isActive():
            self.reader.start(self.fps)

    def _apply_blur(self) -> None:
        """Attach or drop the blur, whichever the current state calls for.

        Idempotent, because it is called from resize, show and every settings
        change: rebuilding the effect when nothing changed would throw away
        Qt's cached offscreen surface for no reason.
        """
        wanted = (
            self._blur_wanted and self.shape.blurs_well and not self._too_big_to_blur()
        )
        if wanted == self._blur_applied:
            return

        self._blur_applied = wanted
        if wanted:
            effect = QGraphicsBlurEffect(self)
            effect.setBlurRadius(7)
            self.setGraphicsEffect(effect)
        else:
            # None rather than a zero radius: an inactive effect still costs a
            # full offscreen render every frame.
            self.setGraphicsEffect(None)

    def _too_big_to_blur(self) -> bool:
        return self.width() * self.height() > BLUR_AREA_LIMIT

    def resizeEvent(self, event) -> None:
        # The blur decision depends on area, and a widget that grows into a
        # full screen has to give it up on the way.
        super().resizeEvent(event)
        self._apply_blur()

    def showEvent(self, event) -> None:
        # Qt does not deliver resize events to a widget that has never been
        # shown, so a window sized before it was displayed would otherwise
        # keep a blur it is far too large for.
        super().showEvent(event)
        self._apply_blur()

    def apply_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance
        self.palette.accent = appearance.theme.accent
        self.update()

    def set_palette(self, palette: "Palette") -> None:
        self.palette = palette
        self.update()

    def set_scales(self, scales: dict) -> None:
        """How large each shape draws, keyed by shape value, in percent."""
        self.scales = {}
        for name, percent in (scales or {}).items():
            try:
                value = max(MIN_SCALE, min(MAX_SCALE, int(percent)))
            except (TypeError, ValueError):
                continue
            self.scales[name] = value / 100
        self.update()

    def scale_for(self, shape: "Shape") -> float:
        return self.scales.get(shape.value, 1.0)

    def set_artwork(self, pixmap) -> None:
        """The current sleeve, for the turntable. None is fine — it falls back
        to a plain coloured label rather than a hole."""
        self._artwork = pixmap
        if any(layer.wants_artwork for layer in self.layers):
            self.update()

    def _pull(self) -> None:
        target = cava.smooth(self.reader.frame) if self.live else [0.0] * cava.BARS

        # Intensity scales the levels before easing, so it changes how far the
        # shape moves without changing how it moves. Clamped at 1.0 because
        # every shape draws within its own bounds and a value above that would
        # simply paint outside the widget.
        if self.intensity != 1.0:
            target = [min(1.0, value * self.intensity) for value in target]

        # Ease towards the new frame. Rising fast and falling slowly is what
        # makes a visualiser look like sound rather than like noise.
        # The rates below were chosen at 60fps. Left alone at 24fps the wave
        # would visibly crawl, so they are scaled by how much longer each frame
        # now lasts — the motion then looks the same at any rate.
        step = min(3.0, 60 / max(1, self.fps))
        rise = min(0.95, 0.6 * step)
        fall = min(0.95, 0.18 * step)

        eased = []
        for index, value in enumerate(target):
            previous = self.drawn[index] if index < len(self.drawn) else 0.0
            rate = rise if value > previous else fall
            eased.append(previous + (value - previous) * rate)

        # Repainting an unchanged frame is pure waste, and it is the common
        # case: paused, silent, or between tracks, the wave sits flat while the
        # timer keeps firing sixty times a second. Once it has settled, stop
        # drawing until something actually moves.
        # `strict` on purpose: comparing a short frame against a long one
        # silently compares the prefix and calls the wave settled, which would
        # freeze the visualiser on a malformed frame rather than redraw it.
        moving = any(layer.animated for layer in self.layers)
        settled = (
            len(eased) == len(self.drawn)
            and all(abs(new - old) < SETTLED
                    for new, old in zip(eased, self.drawn, strict=True))
            and not (self.live and moving)
        )
        self.drawn = eased

        # Time only passes while there is sound: a record that keeps spinning
        # through a pause is a record nobody is playing.
        loudness = max(eased) if eased else 0.0
        if self.live:
            seconds = 1.0 / max(1, self.fps)
            # 33 1/3 rpm is 200 degrees a second, which is what a record does.
            self._spin = (self._spin + 200.0 * seconds) % 360.0
            self.palette.advance(seconds, loudness)
        if not settled:
            self.update()

    # ── Painting ──────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        values = self.drawn
        if not values:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        colour = QColor(self.appearance.theme.accent)
        for layer in self.layers:
            painter.save()
            self._paint_layer(painter, layer, values, colour)
            painter.restore()

        painter.end()

    def _paint_layer(self, painter: QPainter, shape: "Shape",
                     values: list[float], colour: QColor) -> None:
        """Draw one shape. Every layer in a stack comes through here."""
        scale = self.scale_for(shape)

        if shape.hanging:
            # Flipped about the middle, then drawn by the ordinary painter.
            # One implementation of bars, two directions.
            painter.translate(0, self.height())
            painter.scale(1, -1)
            shape = shape.upright

        if scale != 1.0:
            # Scaled about the point the shape grows from, so making one bigger
            # does not also move it: a radial figure grows out of the middle,
            # and a bar grows off the floor it stands on.
            if shape.radial:
                centre_x, centre_y = self.width() / 2, self.height() / 2
                painter.translate(centre_x, centre_y)
                painter.scale(scale, scale)
                painter.translate(-centre_x, -centre_y)
            else:
                painter.translate(0, self.height())
                painter.scale(1, scale)
                painter.translate(0, -self.height())

        if shape is Shape.BARS:
            self._paint_bars(painter, values, colour)
        elif shape is Shape.BLOCKS:
            self._paint_blocks(painter, values, colour)
        elif shape is Shape.DOTS:
            self._paint_dots(painter, values, colour)
        elif shape is Shape.RADIAL:
            self._paint_radial(painter, values, colour)
        elif shape is Shape.RADIAL_MIRROR:
            self._paint_radial(painter, values, colour, mirrored=True)
        elif shape is Shape.RADIAL_BARS:
            self._paint_radial_spokes(painter, values, colour, style="bars")
        elif shape is Shape.RADIAL_LINES:
            self._paint_radial_spokes(painter, values, colour, style="lines")
        elif shape is Shape.RADIAL_BLOOM:
            self._paint_radial_spokes(painter, values, colour, style="bloom")
        elif shape is Shape.RADIAL_DOTS:
            self._paint_radial_dots(painter, values, colour)
        elif shape is Shape.TURNTABLE:
            self._paint_turntable(painter, values)
        elif shape is Shape.RIBBONS:
            self._paint_ribbons(painter, values)
        elif shape is Shape.TUNNEL:
            self._paint_tunnel(painter, values)
        elif shape is Shape.STARFIELD:
            self._paint_starfield(painter, values)
        elif shape is Shape.LINE:
            self._paint_wave(painter, values, colour, mirrored=False, filled=False)
        elif shape is Shape.MIRROR:
            self._paint_wave(painter, values, colour, mirrored=True)
        else:
            self._paint_wave(painter, values, colour, mirrored=False)

    def _paint_wave(self, painter: QPainter, values: list[float],
                    colour: QColor, *, mirrored: bool, filled: bool = True) -> None:
        width = self.width()
        height = self.height()
        count = len(values)
        if count < 2 or width <= 0:
            return

        baseline = height / 2 if mirrored else height
        reach = (height / 2) if mirrored else height

        path = QPainterPath()
        path.moveTo(0, baseline)
        for index, value in enumerate(values):
            x = index * width / (count - 1)
            path.lineTo(x, baseline - value * reach)
        path.lineTo(width, baseline)

        if mirrored:
            for index in range(count - 1, -1, -1):
                x = index * width / (count - 1)
                path.lineTo(x, baseline + values[index] * reach)
            path.lineTo(0, baseline)

        path.closeSubpath()

        if filled:
            if self.palette.single:
                fill = QColor(self.palette.colour(0, count))
                fill.setAlphaF(self.alpha)
                painter.fillPath(path, fill)
            else:
                # A left-to-right gradient over the same band order the shape
                # itself uses, so the colours line up with the peaks.
                gradient = QLinearGradient(0, 0, width, 0)
                for stop in range(9):
                    at = stop / 8
                    band = QColor(self.palette.colour(int(at * (count - 1)), count))
                    band.setAlphaF(self.alpha)
                    gradient.setColorAt(at, band)
                painter.fillPath(path, gradient)

        # A brighter line on top of the fill, so the shape stays legible on the
        # very dark backgrounds this app is usually looked at on.
        line = QColor(self.palette.colour(count // 2, count))
        line.setAlphaF(min(1.0, self.alpha * (7.0 if not filled else 3.5)))
        painter.setPen(QPen(line, 2.0 if not filled else 1.5))
        painter.drawPath(path)

    def _paint_bars(self, painter: QPainter, values: list[float], colour: QColor) -> None:
        width = self.width()
        height = self.height()
        count = len(values)
        if not count or width <= 0:
            return

        slot = width / count
        bar = max(1.0, slot - 2)
        radius = min(3.0, bar / 2)

        for index, value in enumerate(values):
            tall = max(1.0, value * height)
            fill = QColor(self.palette.colour(index, count, value))
            fill.setAlphaF(min(1.0, self.alpha * 4))
            path = QPainterPath()
            path.addRoundedRect(index * slot, height - tall, bar, tall, radius, radius)
            painter.fillPath(path, fill)

    def _paint_blocks(self, painter: QPainter, values: list[float], colour: QColor) -> None:
        """Bars cut into segments, like a hi-fi's LED meter.

        The segment height is fixed rather than a fraction of the bar, so the
        blocks stay the same size as the level moves and the eye reads the
        *count* of them — which is the whole point of a meter like this.
        """
        width, height = self.width(), self.height()
        count = len(values)
        if not count or width <= 0 or height <= 0:
            return

        slot = width / count
        bar = max(1.0, slot - 2)
        segment = max(3.0, height / 14)
        gap = max(1.0, segment * 0.35)
        step = segment + gap

        for index, value in enumerate(values):
            tall = value * height
            lit = int(tall // step)
            for level in range(lit + 1):
                y = height - (level + 1) * step + gap
                if y < 0:
                    break
                # Higher segments burn brighter, so a peak reads at a glance.
                fraction = level / max(1, height / step)
                shade = QColor(self.palette.colour(index, count, value))
                shade.setAlphaF(min(1.0, self.alpha * (3.0 + fraction * 5.0)))
                painter.fillRect(int(index * slot), int(y), int(bar), int(segment), shade)

    def _paint_dots(self, painter: QPainter, values: list[float], colour: QColor) -> None:
        """One dot per band, riding on top of its level."""
        width, height = self.width(), self.height()
        count = len(values)
        if not count or width <= 0:
            return

        slot = width / count
        radius = max(1.5, min(slot / 2.5, height / 12))

        painter.setPen(Qt.PenStyle.NoPen)
        for index, value in enumerate(values):
            bright = QColor(self.palette.colour(index, count, value))
            bright.setAlphaF(min(1.0, self.alpha * 5))
            painter.setBrush(bright)
            x = index * slot + slot / 2
            y = height - max(radius, value * (height - radius))
            painter.drawEllipse(QPointF(x, y), radius, radius)

        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _ring(self) -> tuple[QPointF, float, float]:
        """Where a radial shape sits: centre, inner radius, how far it reaches.

        Shared by every radial variant so they are all the same size and can be
        switched between without the visualiser appearing to jump.
        """
        width, height = self.width(), self.height()
        centre = QPointF(width / 2, height / 2)
        smaller = min(width, height)
        return centre, smaller * 0.18, smaller * 0.30

    @staticmethod
    def _around(index: int, count: int) -> float:
        """The angle for band `index`, starting at the top and going clockwise."""
        return (index / count) * 2 * math.pi - math.pi / 2

    @staticmethod
    def _mirrored_spectrum(values: list[float]) -> list[float]:
        """The spectrum out one side of the circle and back the other.

        cava's bands run low to high, so mapping them straight around a ring
        piles every bass frequency into one quadrant and leaves the opposite
        side flat — the shape ends up lopsided and looks broken rather than
        rhythmic. Running the spectrum out and back makes the figure symmetric
        about its vertical axis, which is what a radial visualiser is for.
        """
        return values + values[::-1]

    def _paint_radial(self, painter: QPainter, values: list[float],
                      colour: QColor, *, mirrored: bool = False) -> None:
        """The spectrum wrapped into a ring — the shape for a full screen.

        Bands run all the way round, so the first and last are neighbours; the
        values are read as a loop to keep the seam from showing. Mirrored draws
        the same curve inwards as well, which closes the ring into a band.
        """
        values = self._mirrored_spectrum(values)
        count = len(values)
        if count < 2 or self.width() <= 0 or self.height() <= 0:
            return

        centre, inner, reach = self._ring()

        def ring_path(direction: int) -> QPainterPath:
            path = QPainterPath()
            for index in range(count + 1):
                angle = self._around(index, count)
                distance = inner + direction * values[index % count] * reach
                point = QPointF(
                    centre.x() + math.cos(angle) * distance,
                    centre.y() + math.sin(angle) * distance,
                )
                path.moveTo(point) if index == 0 else path.lineTo(point)
            path.closeSubpath()
            return path

        outward = ring_path(1)

        loudest = max(values) if values else 0.0
        fill = QColor(self.palette.colour(0, count, loudest))
        fill.setAlphaF(self.alpha)
        line = QColor(self.palette.colour(count // 2, count, loudest))
        line.setAlphaF(min(1.0, self.alpha * 4))

        if mirrored:
            # The band between the two curves, rather than each on its own:
            # subtracting the inner from the outer leaves a ring that thickens
            # with the music instead of two rings that happen to overlap.
            band = outward.subtracted(ring_path(-1))
            painter.fillPath(band, fill)
            painter.setPen(QPen(line, 1.5))
            painter.drawPath(band)
            return

        painter.fillPath(outward, fill)
        painter.setPen(QPen(line, 2.0))
        painter.drawPath(outward)

    def _paint_radial_spokes(self, painter: QPainter, values: list[float],
                             colour: QColor, *, style: str) -> None:
        """Bands as spokes around a ring.

        Three weights of the same idea: `bars` are thick and capped, `lines`
        are hairlines, and `bloom` starts at the centre so the whole thing
        opens out like a flower rather than sitting on a ring.
        """
        values = self._mirrored_spectrum(values)
        count = len(values)
        if not count or self.width() <= 0 or self.height() <= 0:
            return

        centre, inner, reach = self._ring()
        if style == "bloom":
            inner = 0.0
            reach = min(self.width(), self.height()) * 0.46

        widths = {"bars": max(2.0, reach / 14), "lines": 1.5, "bloom": 2.5}
        alphas = {"bars": 4.0, "lines": 6.0, "bloom": 5.0}

        pen = QPen(QColor(colour), widths[style])
        pen.setCapStyle(Qt.PenCapStyle.RoundCap if style != "lines"
                        else Qt.PenCapStyle.FlatCap)

        for index, value in enumerate(values):
            angle = self._around(index, count)
            cosine, sine = math.cos(angle), math.sin(angle)
            length = max(1.0, value * reach)

            shade = QColor(self.palette.colour(index, count, value))
            # Louder bands burn brighter, which is what stops a ring of spokes
            # reading as a flat asterisk.
            shade.setAlphaF(min(1.0, self.alpha * alphas[style] * (0.35 + value)))
            pen.setColor(shade)
            painter.setPen(pen)

            painter.drawLine(
                QPointF(centre.x() + cosine * inner, centre.y() + sine * inner),
                QPointF(centre.x() + cosine * (inner + length),
                        centre.y() + sine * (inner + length)),
            )

    def _paint_radial_dots(self, painter: QPainter, values: list[float],
                           colour: QColor) -> None:
        """One dot per band, riding out from the ring."""
        values = self._mirrored_spectrum(values)
        count = len(values)
        if not count or self.width() <= 0 or self.height() <= 0:
            return

        centre, inner, reach = self._ring()
        radius = max(1.5, reach / 18)

        painter.setPen(Qt.PenStyle.NoPen)
        for index, value in enumerate(values):
            angle = self._around(index, count)
            distance = inner + value * reach

            shade = QColor(self.palette.colour(index, count, value))
            shade.setAlphaF(min(1.0, self.alpha * 5 * (0.4 + value)))
            painter.setBrush(shade)
            painter.drawEllipse(
                QPointF(centre.x() + math.cos(angle) * distance,
                        centre.y() + math.sin(angle) * distance),
                radius, radius,
            )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    # ── Turntable ─────────────────────────────────────────────────

    def _paint_turntable(self, painter: QPainter, values: list[float]) -> None:
        """A record going round, with the sleeve art where the label sits.

        The grooves are the spectrum: each ring is one band, and its thickness
        and brightness track that band. The whole disc turns at 33 1/3 rpm
        while sound is playing — the real speed, because a record turning at
        an invented rate looks wrong to anyone who has watched one.
        """
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return

        centre = QPointF(width / 2, height / 2)
        outer = min(width, height) * 0.46
        label = outer * 0.36

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 10, 12))
        painter.drawEllipse(centre, outer, outer)

        # Grooves, from the outside in. Reversed so that bass — the loudest
        # and widest movement — sits at the edge where there is room for it.
        count = len(values)
        span = outer - label
        for index in range(count):
            value = values[count - 1 - index]
            radius = outer - (index / max(1, count)) * span
            colour = QColor(self.palette.colour(index, count, value))
            # Kept faint: a record is black with grooves catching the light,
            # not a disc of solid colour. Loud bands lift out of the dark.
            colour.setAlphaF(min(0.7, self.alpha * (0.18 + value * 2.4)))
            painter.setPen(QPen(colour, max(0.5, span / count * (0.25 + value * 0.7))))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, radius, radius)

        painter.setPen(Qt.PenStyle.NoPen)

        # The label, turning with the record.
        painter.save()
        painter.translate(centre)
        painter.rotate(self._spin)

        circle = QPainterPath()
        circle.addEllipse(QPointF(0, 0), label, label)
        painter.setClipPath(circle)

        if self._artwork is not None and not self._artwork.isNull():
            side = int(label * 2) + 2
            art = self._artwork.scaled(
                side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            # Centred on the *scaled* pixmap: expanding to fill a square leaves
            # it wider than the label, and drawing from -label puts a landscape
            # sleeve half off the edge.
            painter.drawPixmap(int(-art.width() / 2), int(-art.height() / 2), art)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.palette.colour(0, 1, 1.0))
            painter.drawEllipse(QPointF(0, 0), label, label)

        # Still clipped to the label, so this is a wedge of the label rather
        # than a bar laid across the whole record — it is what makes the
        # rotation readable on artwork that happens to be symmetrical.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawRect(int(label * 0.35), -2, int(label), 4)

        painter.setClipping(False)

        painter.setBrush(QColor(0, 0, 0))
        painter.drawEllipse(QPointF(0, 0), label * 0.085, label * 0.085)
        painter.restore()

    # ── The flowing ones ──────────────────────────────────────────

    def _paint_ribbons(self, painter: QPainter, values: list[float]) -> None:
        """Layered ribbons drifting across each other.

        Each ribbon reads the spectrum at a different offset and lags a little
        further behind, so they separate and re-converge instead of moving as
        one thick line.
        """
        width, height = self.width(), self.height()
        count = len(values)
        if count < 2 or width <= 0:
            return

        painter.setPen(Qt.PenStyle.NoPen)
        ribbons = 5

        for ribbon in range(ribbons):
            drift = self._spin / 90.0 + ribbon * 0.7
            thickness = height * (0.05 + 0.05 * (ribbon % 2))

            top = QPainterPath()
            bottom: list[QPointF] = []

            for index in range(count):
                value = values[(index + ribbon * 7) % count]
                x = index * width / (count - 1)
                wobble = math.sin(index / 6.0 + drift) * height * 0.16
                y = height * 0.5 + wobble - value * height * 0.26
                top.moveTo(x, y) if index == 0 else top.lineTo(x, y)
                bottom.append(QPointF(x, y + thickness * (0.4 + value)))

            # Closed back along its own underside, so each ribbon is a band of
            # its own rather than everything below it being filled in.
            for point in reversed(bottom):
                top.lineTo(point)
            top.closeSubpath()

            colour = QColor(self.palette.colour(ribbon, ribbons, max(values)))
            colour.setAlphaF(min(0.9, self.alpha * 2.2))
            painter.fillPath(top, colour)

    def _paint_tunnel(self, painter: QPainter, values: list[float]) -> None:
        """Rings rushing outward, the way a tunnel comes at you.

        Each ring is one band; the phase pushes them out so a loud passage
        arrives as a wave travelling towards the edge of the screen.
        """
        width, height = self.width(), self.height()
        count = len(values)
        if not count or width <= 0:
            return

        centre = QPointF(width / 2, height / 2)
        furthest = math.hypot(width, height) * 0.5
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # A ring per band is fifty rings, which overlap into a solid disc and
        # show no depth at all. A dozen, each reading a slice of the spectrum,
        # leaves the dark gaps that make it a tunnel.
        rings = 12
        per_ring = max(1, count // rings)

        for ring in range(rings):
            slice_ = values[ring * per_ring:(ring + 1) * per_ring]
            value = max(slice_) if slice_ else 0.0

            position = ((ring / rings) + self._spin / 360.0) % 1.0
            radius = position * furthest
            if radius < 2:
                continue

            colour = QColor(self.palette.colour(ring, rings, value))
            # Fading with distance is what turns concentric circles into depth.
            colour.setAlphaF(min(1.0, self.alpha * (1.0 - position) * (2.5 + value * 7)))
            painter.setPen(QPen(colour, max(1.0, 2 + value * 14)))
            painter.drawEllipse(centre, radius, radius)

    def _paint_starfield(self, painter: QPainter, values: list[float]) -> None:
        """Points thrown outward from the middle, brighter as they go.

        The angle of each point is fixed by its band, so a frequency always
        appears in the same direction and the field reads as a spectrum rather
        than as noise.
        """
        width, height = self.width(), self.height()
        count = len(values)
        if not count or width <= 0:
            return

        centre = QPointF(width / 2, height / 2)
        furthest = math.hypot(width, height) * 0.5
        painter.setPen(Qt.PenStyle.NoPen)

        for index, value in enumerate(values):
            angle = (index / count) * 2 * math.pi
            for step in range(3):
                position = ((index * 0.13) + step / 3.0 + self._spin / 360.0) % 1.0
                distance = position * furthest
                size = max(1.0, position * (1.5 + value * 7))

                colour = QColor(self.palette.colour(index, count, value))
                colour.setAlphaF(min(1.0, self.alpha * position * (2 + value * 9)))
                painter.setBrush(colour)
                painter.drawEllipse(
                    QPointF(centre.x() + math.cos(angle) * distance,
                            centre.y() + math.sin(angle) * distance),
                    size, size,
                )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


class FullscreenVisualizer(QWidget):
    """The visualiser, and nothing else, filling the screen.

    Its own window rather than a mode of the main one: going full screen
    should not disturb the layout underneath, and closing it should put
    everything back exactly as it was without a relayout.
    """

    closed = Signal()
    previous_requested = Signal()
    toggle_requested = Signal()
    next_requested = Signal()

    def __init__(self, appearance: Appearance, *, shape: Shape = Shape.RADIAL,
                 alpha: float = 0.15, blur: bool = True, intensity: float = 1.0,
                 fps: int = 60, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.appearance = appearance

        self.setWindowTitle("Rose Bouquet")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.visualizer = Visualizer(
            appearance, shape=shape, height=240, blur=blur,
            alpha=alpha, intensity=intensity, fps=fps, parent=self,
        )
        # Clicking anywhere should leave, and the visualiser covers everything.
        self.visualizer.clicked.connect(self.close)
        layout.addWidget(self.visualizer, 1)

        self.caption = QLabel("")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setContentsMargins(0, 0, 0, 12)
        layout.addWidget(self.caption)

        # A transport, because full screen is where somebody sits back and
        # watches — and reaching for the window behind it to skip a track
        # defeats the point of being full screen at all.
        transport = QHBoxLayout()
        transport.setContentsMargins(0, 0, 0, 36)
        transport.addStretch(1)

        for text, signal, tip in (
            ("⏮", self.previous_requested, "Previous"),
            ("⏵", self.toggle_requested, "Play or pause"),
            ("⏭", self.next_requested, "Next"),
        ):
            button = QPushButton(text)
            button.setObjectName("Quiet")
            button.setFixedSize(52, 40)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(signal.emit)
            transport.addWidget(button)
            if text == "⏵":
                self.play_button = button

        transport.addStretch(1)
        layout.addLayout(transport)

        self.restyle(appearance)

    # ── Contents ──────────────────────────────────────────────────

    def set_track(self, title: str, artist: str) -> None:
        self.caption.setText(f"{title} — {artist}" if artist else title)

    def set_live(self, live: bool) -> None:
        self.visualizer.set_live(live)
        self.play_button.setText("⏸" if live else "⏵")

    def restyle(self, appearance: Appearance) -> None:
        self.appearance = appearance
        theme = appearance.theme
        self.setStyleSheet(f"background-color: {theme.background};")
        self.caption.setStyleSheet(
            f"color: {theme.text_dim}; background: transparent; font-size: 15px;"
        )
        for button in self.findChildren(QPushButton):
            button.setStyleSheet(
                f"color: {theme.text}; background: transparent; border: none;"
                f" font-size: 20px;"
            )
        self.visualizer.apply_appearance(appearance)

    def apply(self, *, shape: Shape, alpha: float, blur: bool,
              intensity: float, fps: int, layers=None) -> None:
        self.visualizer.set_layers(layers or [shape])
        self.visualizer.alpha = alpha
        self.visualizer.set_blur(blur)
        self.visualizer.set_intensity(intensity)
        self.visualizer.set_fps(fps)

    # ── Showing and leaving ───────────────────────────────────────

    def open(self) -> None:
        self.showFullScreen()
        self.visualizer.start()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event) -> None:
        # Escape, F11 and Q are all "let me out" — a full-screen window with no
        # chrome has to answer every reasonable guess.
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F11, Qt.Key.Key_Q):
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.visualizer.stop()
        self.closed.emit()
        super().closeEvent(event)
