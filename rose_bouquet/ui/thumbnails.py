"""Thumbnails, fetched once and kept.

A wall of YouTube results without pictures is a list of filenames. Fetching them
naively is worse: twenty-four HTTP requests on the interface thread freezes the
window, and refetching the same image every time a view redraws wastes both
ends' bandwidth.

So: fetched in the background, cached in memory as a ready-scaled pixmap, and
cached on disk as the original bytes so they survive a restart. A label asks for
one and gets a placeholder immediately; the picture replaces it when it arrives,
if the label is still there to receive it.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from rose_bouquet.core.library import data_dir
from rose_bouquet.ui import tasks
from rose_bouquet.ui.theme import Appearance

logger = logging.getLogger(__name__)

#: Scaled pixmaps, keyed by url and size.
_memory: dict[tuple[str, int, int], QPixmap] = {}
_MEMORY_LIMIT = 300

#: Urls already being fetched, so twenty rows asking for the same picture make
#: one request rather than twenty.
_in_flight: set[str] = set()

TIMEOUT = 12


def cache_dir() -> Path:
    folder = data_dir() / "thumbnails"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def cache_path(url: str) -> Path:
    return cache_dir() / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".img")  # noqa: S324


def youtube_thumbnail(video_id: str, *, big: bool = False) -> str:
    """YouTube's own thumbnail url for a video id.

    Used when a listing did not include one — flat extraction often omits them,
    and this is a stable public url rather than something scraped.
    """
    if not video_id:
        return ""
    quality = "hqdefault" if big else "mqdefault"
    return f"https://i.ytimg.com/vi/{video_id}/{quality}.jpg"


def fetch_bytes(url: str) -> bytes:
    """The image, from disk if it has been seen before."""
    path = cache_path(url)
    if path.exists():
        try:
            return path.read_bytes()
        except OSError:
            pass

    try:
        import requests

        response = requests.get(url, timeout=TIMEOUT,
                                headers={"User-Agent": "Mozilla/5.0 (rose-bouquet)"})
        response.raise_for_status()
        data = response.content
    except Exception as exc:                      # noqa: BLE001 — a picture is not worth raising over
        logger.debug("could not fetch %s: %s", url, exc)
        return b""

    try:
        path.write_bytes(data)
    except OSError:
        pass
    return data


def rounded(pixmap: QPixmap, width: int, height: int, radius: int) -> QPixmap:
    """Scaled to fill, cropped to centre, with rounded corners."""
    result = QPixmap(width, height)
    result.fill(Qt.GlobalColor.transparent)

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    shape = QPainterPath()
    shape.addRoundedRect(0, 0, width, height, radius, radius)
    painter.setClipPath(shape)

    scaled = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    painter.drawPixmap(int((width - scaled.width()) / 2),
                       int((height - scaled.height()) / 2), scaled)
    painter.end()
    return result


def placeholder(width: int, height: int, appearance: Appearance, glyph: str = "▶") -> QPixmap:
    """What is shown until the picture arrives, or instead of one that never does."""
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    shape = QPainterPath()
    shape.addRoundedRect(0, 0, width, height, appearance.style.radius_small,
                         appearance.style.radius_small)
    painter.fillPath(shape, QColor(appearance.theme.placeholder))

    painter.setPen(QColor(appearance.theme.text_dim))
    font = painter.font()
    font.setPointSize(max(9, height // 4))
    painter.setFont(font)
    painter.drawText(0, 0, width, height, Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return pixmap


class Thumbnail(QLabel):
    """A label that fills itself in when its picture arrives."""

    def __init__(
        self,
        url: str,
        width: int,
        height: int,
        appearance: Appearance,
        *,
        glyph: str = "▶",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.url = url
        self.target = (width, height)
        self.appearance = appearance

        self.setFixedSize(width, height)
        self.setStyleSheet("background: transparent;")
        self.setPixmap(placeholder(width, height, appearance, glyph))

        if not url:
            return

        key = (url, width, height)
        cached = _memory.get(key)
        if cached is not None:
            self.setPixmap(cached)
            return

        # A label can be destroyed before its picture arrives — scrolling a long
        # list does exactly that — so the callback checks before touching it.
        tasks.run(fetch_bytes, url, on_done=self._arrived)

    def _arrived(self, data: bytes) -> None:
        try:
            if not data or (not self.isVisible() and self.parent() is None):
                return
        except RuntimeError:
            return                                 # the label is gone

        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return

        width, height = self.target
        finished = rounded(pixmap, width, height, self.appearance.style.radius_small)

        if len(_memory) > _MEMORY_LIMIT:
            _memory.clear()
        _memory[(self.url, width, height)] = finished

        try:
            self.setPixmap(finished)
        except RuntimeError:
            pass                                   # destroyed while decoding
