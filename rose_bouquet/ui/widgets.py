"""Track rows, cover art and the small pieces the views are built from."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QImageReader, QPainter, QPainterPath, QPixmap
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
from rose_bouquet.ui import tasks
from rose_bouquet.ui.theme import Appearance

#: Cover art is decoded once per path and per size, because a library view
#: scrolling through hundreds of albums would otherwise decode the same JPEG
#: hundreds of times.
_COVER_CACHE: dict[tuple[str, int], QPixmap] = {}
_CACHE_LIMIT = 400


def _decoded(path: str, size: int) -> QPixmap:
    """The cover, decoded straight to the size it will be drawn at.

    YouTube hands over 1280x720 artwork and a row draws it at 38 pixels.
    Decoding the whole thing and scaling afterwards is most of a millisecond
    of work per cover thrown away — a visible pause the first time a library
    with real artwork is scrolled. A reader told the size it needs decodes
    the image at that size instead.

    Twice the target, so the rounding and the smooth scale below still have
    pixels to work with on a high-DPI screen.
    """
    if not path or not Path(path).exists():
        return QPixmap()

    reader = QImageReader(path)
    reader.setAutoTransform(True)
    natural = reader.size()
    wanted = size * 2
    if natural.isValid() and max(natural.width(), natural.height()) > wanted:
        scaled = natural.scaled(wanted, wanted, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(scaled)

    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


def _art_key(track) -> str:
    """A cache key for a track's art before its location is known."""
    return "track:" + getattr(track, "path", "")


def cover_pixmap(path: str, size: int, appearance: Appearance) -> QPixmap:
    """Rounded cover art at a given size, or a themed placeholder."""
    key = (path or "", size)
    cached = _COVER_CACHE.get(key)
    if cached is not None:
        return cached

    source = _decoded(path, size)

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
    """A square of cover art, which arrives when it arrives.

    Finding the art means reading tags out of an audio file, and turning it
    into a pixmap means decoding a JPEG. Doing both while building a row cost
    about a millisecond and a half per track — which is invisible for one row
    and fourteen seconds for a library of nine hundred, all of it on the thread
    that is supposed to be drawing.

    So a row gets its placeholder immediately and its cover when the work is
    done. Anything already decoded is still set outright, because the cache
    makes that free and a scroll back up should not flicker.
    """

    #: Covers being decoded right now, so a fast scroll does not ask for the
    #: same one ten times. Keyed the same as the pixmap cache.
    _in_flight: set = set()

    def __init__(self, size: int, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.size_px = size
        self.appearance = appearance
        self.setFixedSize(QSize(size, size))
        self.setStyleSheet("background: transparent;")
        self._wanted = ""

    def set_track(self, track: Optional[Track]) -> None:
        # Not `track.cover`: that is only art sitting *beside* the file, and
        # most music keeps its cover inside the tags instead — a library with
        # no `cover.jpg` anywhere would show placeholders from end to end.
        source = getattr(track, "path", "") if track else ""
        self._wanted = source

        ready = _COVER_CACHE.get((_art_key(track), self.size_px)) if track else None
        if ready is not None:
            self.setPixmap(ready)
            return

        # Something to look at now; the real thing follows.
        self.setPixmap(cover_pixmap("", self.size_px, self.appearance))
        if track is not None:
            self._fetch(track)

    def _fetch(self, track: Track) -> None:
        """Find and decode this track's art away from the drawing thread."""
        key = (track.path, self.size_px)
        if key in CoverArt._in_flight:
            return
        CoverArt._in_flight.add(key)

        size, appearance = self.size_px, self.appearance

        def find() -> str:
            return artwork.local_art(track)

        def arrived(path: str) -> None:
            CoverArt._in_flight.discard(key)
            # Decoded here, on the UI thread, because a QPixmap cannot be made
            # off it. The expensive half — finding the art, which means reading
            # tags — has already happened.
            pixmap = cover_pixmap(path, size, appearance)
            _COVER_CACHE[(path or "", size)] = pixmap
            _COVER_CACHE[(_art_key(track), size)] = pixmap
            # The row may have been recycled onto another track by now.
            if self._wanted == track.path:
                self.setPixmap(pixmap)

        def failed(_message: str) -> None:
            CoverArt._in_flight.discard(key)

        tasks.run(find, on_done=arrived, on_error=failed)

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
        # Re-polishing is the expensive half of this, and in a list of a
        # thousand rows exactly two of them are ever changing.
        if playing == self.playing:
            return
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
        art.setObjectName("CardArt")
        art.setPixmap(cover_pixmap(cover, size, appearance))
        art.setFixedSize(size, size)
        layout.addWidget(art)

        name = QLabel(title)
        name.setObjectName("CardTitle")
        name.setWordWrap(True)
        layout.addWidget(name)

        if subtitle:
            caption = QLabel(subtitle)
            caption.setObjectName("Subtle")
            caption.setWordWrap(True)
            layout.addWidget(caption)

        layout.addStretch(1)

    def apply_appearance(self, appearance: Appearance) -> None:
        """Adopt a palette.

        Nothing to set, for the same reason a track row has nothing to set:
        every colour on a card now comes from the window's stylesheet by
        object name. Each card used to write three stylesheets of its own in
        its constructor, which on a wall of six hundred albums was three
        hundred milliseconds of Qt parsing the same rules over and over.
        """
        self.appearance = appearance

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


class UpdateBar(QWidget):
    """A new version is out — said in the window, not in a settings tab.

    Separate from `Banner` because a banner is a toast: it says its piece and
    disappears after six seconds. An update notice that vanishes while you are
    looking at something else is the same as no notice at all, which is how
    the old one worked — a button in Settings, and you had to already know it
    was there to go and press it. This stays until it is answered.
    """

    update_requested = Signal()
    dismissed = Signal()

    def __init__(self, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.appearance = appearance
        self.setVisible(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(10)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        row.addWidget(self.message, 1)

        self.update_button = QPushButton("Update now")
        self.update_button.clicked.connect(self.update_requested.emit)
        row.addWidget(self.update_button)

        self.later = QPushButton("Later")
        self.later.clicked.connect(self._dismiss)
        row.addWidget(self.later)

        self.apply_appearance(appearance)

    def announce(self, version: str) -> None:
        self.message.setText(f"Version {version} is available.")
        self.update_button.setEnabled(True)
        self.setVisible(True)

    def working(self, text: str = "Updating…") -> None:
        """Mid-update: the buttons stop, the bar stays and says what happened."""
        self.message.setText(text)
        self.update_button.setEnabled(False)

    def finished(self, text: str) -> None:
        self.message.setText(text)
        self.update_button.setVisible(False)
        self.later.setText("Close")

    def _dismiss(self) -> None:
        self.setVisible(False)
        self.dismissed.emit()

    def apply_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance
        theme = appearance.theme
        self.setStyleSheet(
            f"background-color: {theme.accent};"
        )
        self.message.setStyleSheet(
            f"color: {theme.background}; font-weight: 600; background: transparent;")
        for button in (self.update_button, self.later):
            button.setStyleSheet(
                f"color: {theme.text}; background-color: {theme.background};"
                f" border: none; border-radius: 6px; padding: 5px 12px;"
            )
