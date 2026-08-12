"""The visualiser — the same wave as the one in the Hyprland bar.

Gavin's Quickshell config draws cava's output as a filled curve: 50 bars, a
moving-average smoothing pass with a window of 2, filled at low alpha under the
line, then blurred. This is that, in `QPainter`, reading the same cava settings,
so the player and the bar move together.

Three shapes are offered, because a visualiser is decoration and decoration
should be a preference: the wave (the Hyprland one), bars (classic cava), and
a mirrored wave for the full-window background behind the now-playing art.

The reader thread only ever *appends to a list*, and the widget only ever reads
the most recent frame. No locking, no queue: the worst a race can do here is
draw one frame that is half old, sixty times a second, which nobody can see.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsBlurEffect, QSizePolicy, QWidget

from rose_bouquet.core import cava
from rose_bouquet.ui.theme import Appearance

logger = logging.getLogger(__name__)


class Shape(str, Enum):
    WAVE = "wave"
    BARS = "bars"
    MIRROR = "mirror"

    @property
    def label(self) -> str:
        return {Shape.WAVE: "Wave", Shape.BARS: "Bars", Shape.MIRROR: "Mirrored"}[self]


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

    def start(self) -> bool:
        if self.running:
            return True

        self._process = cava.start()
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
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.appearance = appearance
        self.shape = shape
        self.alpha = alpha
        self.live = False

        self.reader = CavaReader.shared()
        self.points: list[float] = [0.0] * cava.BARS
        #: The last drawn frame, eased towards the new one so the fall-off is
        #: smooth even when cava sends a hard zero.
        self.drawn: list[float] = [0.0] * cava.BARS

        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        if blur and shape is not Shape.BARS:
            # The Quickshell one blurs to obscure the individual points; the
            # same trick keeps the curve reading as a glow rather than a graph.
            effect = QGraphicsBlurEffect(self)
            effect.setBlurRadius(7)
            self.setGraphicsEffect(effect)

        self._timer = QTimer(self)
        self._timer.setInterval(1000 // 60)
        self._timer.timeout.connect(self._pull)

    # ── Running ───────────────────────────────────────────────────

    def start(self) -> bool:
        started = self.reader.start()
        self._timer.start()
        return started

    def stop(self) -> None:
        self._timer.stop()

    def set_live(self, live: bool) -> None:
        """Whether audio is playing. When it is not, the wave settles to flat."""
        self.live = live

    def set_shape(self, shape: Shape) -> None:
        self.shape = shape
        self.update()

    def apply_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance
        self.update()

    def _pull(self) -> None:
        target = cava.smooth(self.reader.frame) if self.live else [0.0] * cava.BARS

        # Ease towards the new frame. Rising fast and falling slowly is what
        # makes a visualiser look like sound rather than like noise.
        eased = []
        for index, value in enumerate(target):
            previous = self.drawn[index] if index < len(self.drawn) else 0.0
            rate = 0.6 if value > previous else 0.18
            eased.append(previous + (value - previous) * rate)

        self.drawn = eased
        self.update()

    # ── Painting ──────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        values = self.drawn
        if not values:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        colour = QColor(self.appearance.theme.accent)
        if self.shape is Shape.BARS:
            self._paint_bars(painter, values, colour)
        elif self.shape is Shape.MIRROR:
            self._paint_wave(painter, values, colour, mirrored=True)
        else:
            self._paint_wave(painter, values, colour, mirrored=False)

        painter.end()

    def _paint_wave(self, painter: QPainter, values: list[float],
                    colour: QColor, *, mirrored: bool) -> None:
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

        fill = QColor(colour)
        fill.setAlphaF(self.alpha)
        painter.fillPath(path, fill)

        # A brighter line on top of the fill, so the shape stays legible on the
        # OLED-black backgrounds this app is usually looked at on.
        line = QColor(colour)
        line.setAlphaF(min(1.0, self.alpha * 3.5))
        painter.setPen(QPen(line, 1.5))
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

        fill = QColor(colour)
        fill.setAlphaF(min(1.0, self.alpha * 4))

        for index, value in enumerate(values):
            tall = max(1.0, value * height)
            path = QPainterPath()
            path.addRoundedRect(index * slot, height - tall, bar, tall, radius, radius)
            painter.fillPath(path, fill)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)
