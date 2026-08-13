"""Small drawn icons, for the places a word would be too long and a letter
too cryptic.

Drawn with `QPainter` rather than shipped as image files or borrowed from an
emoji font. Three reasons, in order of how much they matter:

  - **They take the theme's colour.** An emoji is whatever colour the font
    decided, which is wrong on every theme but the one it happened to match.
    These are painted in whatever colour is asked for.
  - **They look the same everywhere.** Emoji render differently on every
    machine — different font, different metrics, sometimes a blank box — and a
    list that is a wall of tofu on someone else's desktop is worse than a list
    of letters.
  - **No asset files.** Nothing to install, nothing to find at runtime, nothing
    to get out of step with the code.

Every icon is drawn inside a 100x100 box and scaled to the size asked for, so
the shapes are written once at a size that is easy to reason about.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

#: Everything is drawn in this coordinate space and scaled down.
CANVAS = 100.0

#: Rendered icons, keyed by what they are and how they look. A feed of sixty
#: rows asks for the same handful of icons over and over.
_CACHE: dict[tuple[str, int, str], QPixmap] = {}
_CACHE_LIMIT = 200


def kind_icon(kind: str, size: int, colour: str) -> QPixmap:
    """An icon for a YouTube Music result kind, in the colour given."""
    key = (kind, size, colour)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / CANVAS, size / CANVAS)

    drawer = _DRAWERS.get(kind, _draw_song)
    drawer(painter, QColor(colour))
    painter.end()

    if len(_CACHE) > _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = pixmap
    return pixmap


# ── The shapes ────────────────────────────────────────────────────

def _draw_song(painter: QPainter, colour: QColor) -> None:
    """A quaver: one notehead, a stem, and a flag."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)

    # The head is an ellipse leaning left, the way a written note does.
    painter.save()
    painter.translate(34, 72)
    painter.rotate(-20)
    painter.drawEllipse(QRectF(-22, -15, 44, 30))
    painter.restore()

    stem = QPainterPath()
    stem.addRoundedRect(QRectF(50, 12, 9, 62), 4, 4)
    painter.fillPath(stem, colour)

    flag = QPainterPath()
    flag.moveTo(59, 14)
    flag.cubicTo(84, 22, 88, 40, 74, 54)
    flag.cubicTo(82, 38, 74, 28, 59, 30)
    flag.closeSubpath()
    painter.fillPath(flag, colour)


def _draw_album(painter: QPainter, colour: QColor) -> None:
    """A disc: an outer ring, a wide groove, and a spindle hole."""
    painter.setBrush(Qt.BrushStyle.NoBrush)

    painter.setPen(QPen(colour, 8))
    painter.drawEllipse(QRectF(10, 10, 80, 80))

    # A faint inner ring reads as a groove and stops it looking like a letter O.
    groove = QColor(colour)
    groove.setAlphaF(0.45)
    painter.setPen(QPen(groove, 5))
    painter.drawEllipse(QRectF(28, 28, 44, 44))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawEllipse(QRectF(43, 43, 14, 14))


def _draw_video(painter: QPainter, colour: QColor) -> None:
    """A clapperboard: the slate, and the striped clapper hinged on top."""
    painter.setPen(Qt.PenStyle.NoPen)

    body = QPainterPath()
    body.addRoundedRect(QRectF(10, 42, 80, 44), 7, 7)
    painter.fillPath(body, colour)

    # The clapper sits at an angle, which is the thing that makes the shape
    # read as a clapperboard rather than as a plain rectangle.
    painter.save()
    painter.translate(10, 40)
    painter.rotate(-9)

    clapper = QPainterPath()
    clapper.addRoundedRect(QRectF(0, -18, 82, 20), 4, 4)
    painter.fillPath(clapper, colour)

    # Stripes, cut out of the clapper so they take the background with them.
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    for offset in (12, 34, 56):
        stripe = QPainterPath()
        stripe.moveTo(offset, -18)
        stripe.lineTo(offset + 9, -18)
        stripe.lineTo(offset + 2, 2)
        stripe.lineTo(offset - 7, 2)
        stripe.closeSubpath()
        painter.fillPath(stripe, QColor(0, 0, 0))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.restore()


def _draw_playlist(painter: QPainter, colour: QColor) -> None:
    """Stacked lines with a play arrow, the usual shorthand for a list."""
    painter.setPen(Qt.PenStyle.NoPen)

    for y in (20, 44, 68):
        width = 78 if y != 68 else 44
        line = QPainterPath()
        line.addRoundedRect(QRectF(10, y, width, 10), 5, 5)
        painter.fillPath(line, colour)

    arrow = QPainterPath()
    arrow.moveTo(64, 60)
    arrow.lineTo(92, 74)
    arrow.lineTo(64, 88)
    arrow.closeSubpath()
    painter.fillPath(arrow, colour)


def _draw_artist(painter: QPainter, colour: QColor) -> None:
    """A microphone, which says performer where a person icon says account."""
    painter.setPen(Qt.PenStyle.NoPen)

    head = QPainterPath()
    head.addRoundedRect(QRectF(36, 10, 28, 46), 14, 14)
    painter.fillPath(head, colour)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(colour, 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawArc(QRectF(24, 30, 52, 46), 200 * 16, 140 * 16)

    painter.drawLine(QPointF(50, 74), QPointF(50, 90))


_DRAWERS = {
    "song": _draw_song,
    "video": _draw_video,
    "album": _draw_album,
    "single": _draw_album,
    "playlist": _draw_playlist,
    "artist": _draw_artist,
}

#: What each icon means, for tooltips — an icon nobody can name is a letter
#: with extra steps.
LABELS = {
    "song": "Song",
    "video": "Video",
    "album": "Album",
    "single": "Single",
    "playlist": "Playlist",
    "artist": "Artist",
}
