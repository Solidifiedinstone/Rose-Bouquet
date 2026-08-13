"""Track rows, cover art and the small pieces the views are built from."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core import artwork
from rose_bouquet.core.library import Track
from rose_bouquet.ui.theme import Appearance

#: Cover art is decoded once per path and per size, because a library view
#: scrolling through hundreds of albums would otherwise decode the same JPEG
#: hundreds of times.
_COVER_CACHE: dict[tuple[str, int], QPixmap] = {}
_CACHE_LIMIT = 400


def cover_pixmap(path: str, size: int, appearance: Appearance) -> QPixmap:
    """Rounded cover art at a given size, or a themed placeholder."""
    key = (path or "", size)
    cached = _COVER_CACHE.get(key)
    if cached is not None:
        return cached

    source = QPixmap(path) if path and Path(path).exists() else QPixmap()

    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    path_shape = QPainterPath()
    radius = min(appearance.style.radius_small, size / 4)
    path_shape.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path_shape)

    if source.isNull():
        painter.fillRect(0, 0, size, size, QColor(appearance.theme.placeholder))
        painter.setPen(QColor(appearance.theme.text_dim))
        font = painter.font()
        font.setPointSize(max(8, int(size / 3)))
        painter.setFont(font)
        painter.drawText(0, 0, size, size, Qt.AlignmentFlag.AlignCenter, "♪")
    else:
        scaled = source.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(
            int((size - scaled.width()) / 2), int((size - scaled.height()) / 2), scaled
        )

    painter.end()

    if len(_COVER_CACHE) > _CACHE_LIMIT:
        _COVER_CACHE.clear()
    _COVER_CACHE[key] = result
    return result


class CoverArt(QLabel):
    """A square of cover art."""

    def __init__(self, size: int, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.size_px = size
        self.appearance = appearance
        self.setFixedSize(QSize(size, size))
        self.setStyleSheet("background: transparent;")

    def set_track(self, track: Optional[Track]) -> None:
        # Not `track.cover`: that is only art sitting *beside* the file, and
        # most music keeps its cover inside the tags instead — a library with
        # no `cover.jpg` anywhere would show placeholders from end to end.
        path = artwork.local_art(track) if track else ""
        self.setPixmap(cover_pixmap(path, self.size_px, self.appearance))

    def apply_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance


class TrackRow(QWidget):
    """One track in a list: number or art, title, artist, album, duration."""

    play_requested = Signal(object)     # Track
    menu_requested = Signal(object, object)

    def __init__(
        self,
        track: Track,
        appearance: Appearance,
        *,
        index: Optional[int] = None,
        show_art: bool = True,
        show_album: bool = True,
        playing: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.track = track
        self.appearance = appearance
        self.playing = playing

        self.setObjectName("TrackRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda point: self.menu_requested.emit(self.track, self.mapToGlobal(point))
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 12, 5)
        layout.setSpacing(12)

        if index is not None:
            number = QLabel(str(index))
            number.setObjectName("Subtle")
            number.setFixedWidth(28)
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(number)

        if show_art:
            self.art = CoverArt(38, appearance)
            self.art.set_track(track)
            layout.addWidget(self.art)

        column = QVBoxLayout()
        column.setSpacing(1)
        column.setContentsMargins(0, 0, 0, 0)

        self.title = QLabel(track.display_title)
        self.title.setObjectName("RowTitle")
        column.addWidget(self.title)

        subtitle = track.display_artist
        if show_album and track.album:
            subtitle = f"{subtitle} — {track.display_album}"
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("Subtle")
        column.addWidget(self.subtitle)

        layout.addLayout(column, 1)

        if track.source == "youtube":
            badge = QLabel("YT")
            badge.setObjectName("Subtle")
            badge.setToolTip("Downloaded from YouTube Music")
            layout.addWidget(badge)

        self.duration = QLabel(track.clock)
        self.duration.setObjectName("Subtle")
        layout.addWidget(self.duration)

        self.apply_appearance(appearance)

    def apply_appearance(self, appearance: Appearance) -> None:
        """Adopt a palette.

        Nothing to set: every colour in a row comes from the window's
        stylesheet by object name, and the playing state is a property that
        stylesheet already selects on. Rows used to write their own two
        stylesheets here, which is what made a long list slow to draw.
        """
        self.appearance = appearance
        self._mark_playing()

    def set_playing(self, playing: bool) -> None:
        self.playing = playing
        self._mark_playing()

    def _mark_playing(self) -> None:
        # Qt only re-evaluates property selectors when told to, and the rule
        # lives on the title rather than the row, so the title is what has to
        # be re-polished.
        self.setProperty("playing", "true" if self.playing else "false")
        self.style().unpolish(self.title)
        self.style().polish(self.title)

    def mouseDoubleClickEvent(self, event) -> None:
        self.play_requested.emit(self.track)
        super().mouseDoubleClickEvent(event)


class Card(QWidget):
    """A cover with a title under it — an album, a playlist, a YT Music result."""

    activated = Signal(object)
    menu_requested = Signal(object, object)

    def __init__(
        self,
        payload,
        title: str,
        subtitle: str,
        cover: str,
        appearance: Appearance,
        *,
        size: int = 150,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.payload = payload
        self.appearance = appearance

        self.setObjectName("Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(size + 20)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda point: self.menu_requested.emit(self.payload, self.mapToGlobal(point))
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        art = QLabel()
        art.setPixmap(cover_pixmap(cover, size, appearance))
        art.setFixedSize(size, size)
        art.setStyleSheet("background: transparent;")
        layout.addWidget(art)

        name = QLabel(title)
        name.setWordWrap(True)
        name.setStyleSheet(
            f"color: {appearance.theme.text}; font-weight: 600; background: transparent;"
        )
        layout.addWidget(name)

        if subtitle:
            caption = QLabel(subtitle)
            caption.setObjectName("Subtle")
            caption.setWordWrap(True)
            layout.addWidget(caption)

        layout.addStretch(1)
        self.apply_appearance(appearance)

    def apply_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance
        self.setStyleSheet(
            f"#Card {{ background-color: transparent;"
            f" border-radius: {appearance.style.radius}px; }}"
            f"#Card:hover {{ background-color: {appearance.theme.panel}; }}"
        )

    def mouseDoubleClickEvent(self, event) -> None:
        self.activated.emit(self.payload)
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.payload)
        super().mousePressEvent(event)


class SectionHeading(QWidget):
    """A heading, optionally with a count and a button."""

    def __init__(
        self,
        text: str,
        appearance: Appearance,
        *,
        count: Optional[int] = None,
        action: Optional[tuple[str, Callable[[], None]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 8, 2, 4)
        layout.setSpacing(8)

        label = QLabel(text)
        label.setStyleSheet(
            f"color: {appearance.theme.text}; font-weight: 700;"
            f" font-size: {appearance.style.font_size + 2}px; background: transparent;"
        )
        layout.addWidget(label)

        if count is not None:
            counter = QLabel(str(count))
            counter.setObjectName("Subtle")
            layout.addWidget(counter)

        layout.addStretch(1)

        if action is not None:
            text, handler = action
            button = QPushButton(text)
            button.setObjectName("Quiet")
            button.clicked.connect(handler)
            layout.addWidget(button)


class Banner(QLabel):
    """A one-line message across the top of the window."""

    def __init__(self, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.appearance = appearance
        self.setVisible(False)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def show_message(self, text: str, *, kind: str = "info", seconds: int = 6) -> None:
        from PySide6.QtCore import QTimer

        theme = self.appearance.theme
        colour = {
            "info": theme.accent, "success": theme.success,
            "warning": theme.warning, "error": theme.error,
        }.get(kind, theme.accent)

        self.setText(text)
        self.setStyleSheet(
            f"background-color: {colour}; color: {theme.background};"
            f" padding: 9px 14px; font-weight: 600;"
        )
        self.setVisible(True)

        def hide() -> None:
            # A newer message must not be cleared by an older one's timer.
            if self.text() == text:
                self.setVisible(False)

        QTimer.singleShot(seconds * 1000, hide)

    def apply_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance
