"""Settings: appearance, library, visualiser, downloads, serving, and about.

Everything applies live, as in the other Rose apps — dragging a slider repaints
the window behind the dialog rather than waiting for an OK button.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core import autostart
from rose_bouquet.core.library import music_dir
from rose_bouquet.core.server import DEFAULT_PORT, new_password
from rose_bouquet.ui import tasks
from rose_bouquet.ui.branding import APP_NAME, APP_TAGLINE, ORGANISATION, rose_widget
from rose_bouquet.ui.preferences import (
    STYLE_AXES,
    STYLE_RANGES,
    Preferences,
    load_credentials,
    save_credentials,
)
from rose_bouquet.ui.theme import Appearance, list_style_names, list_theme_names
from rose_bouquet.ui.visualizer import (
    DEFAULT_COLOURS,
    MAX_COLOURS,
    MAX_SCALE,
    MIN_COLOURS,
    MIN_SCALE,
    ColourMode,
    ColourMotion,
    Shape,
)

APPLY_DELAY_MS = 40


class _StayOpenMenu(QMenu):
    """A menu that stays open while tick boxes are being ticked.

    Qt closes a menu the moment any action is triggered, which for a
    multiple-choice menu means reopening it for every single choice.
    """

    def mouseReleaseEvent(self, event) -> None:
        action = self.activeAction()
        if action is not None and action.isCheckable() and action.isEnabled():
            action.trigger()
            return
        super().mouseReleaseEvent(event)


class SettingsDialog(QDialog):
    """Everything configurable, applied as it changes."""

    appearance_changed = Signal(Appearance)
    library_changed = Signal()
    visualizer_changed = Signal()
    server_changed = Signal()

    def __init__(self, preferences: Preferences,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.preferences = preferences
        self.appearance = preferences.appearance()

        self.setWindowTitle(f"{APP_NAME} — Settings")
        self.resize(600, 680)
        self.setStyleSheet(self.appearance.stylesheet())

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(APPLY_DELAY_MS)
        self._apply_timer.timeout.connect(self._apply)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._appearance_tab(), "Appearance")
        tabs.addTab(self._visualizer_tab(), "Visualiser")
        tabs.addTab(self._library_tab(), "Library")
        tabs.addTab(self._downloads_tab(), "Downloads")
        tabs.addTab(self._server_tab(), "Serving")
        tabs.addTab(self._about_tab(), "About")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ── Appearance ────────────────────────────────────────────────

    def _appearance_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        form = QFormLayout(body)
        form.setSpacing(12)

        self.theme_picker = QComboBox()
        for key, name in list_theme_names():
            self.theme_picker.addItem(name, key)
        self._select(self.theme_picker, self.preferences.theme)
        self.theme_picker.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("Colours", self.theme_picker)

        self.style_picker = QComboBox()
        for key, name in list_style_names():
            self.style_picker.addItem(name, key)
        self._select(self.style_picker, self.preferences.style)
        self.style_picker.currentIndexChanged.connect(self._on_style_changed)
        form.addRow("Shape", self.style_picker)

        note = QLabel(
            "Colours and shape are independent — any theme works with any style, "
            "and the same palettes are in Rose GameLab and Rose Productivity."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        form.addRow(note)

        self.axis_widgets: dict[str, QWidget] = {}
        for axis, (label, kind) in STYLE_AXES.items():
            widget = self._axis_widget(axis, kind)
            if widget is not None:
                self.axis_widgets[axis] = widget
                form.addRow(label, widget)

        reset = QPushButton("Reset adjustments")
        reset.clicked.connect(self._reset_overrides)
        form.addRow("", reset)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return page

    @staticmethod
    def _select(picker: QComboBox, key: str) -> None:
        index = picker.findData(key)
        if index >= 0:
            picker.setCurrentIndex(index)

    def _axis_widget(self, axis: str, kind: str) -> Optional[QWidget]:
        value = self.preferences.value_for(axis)

        if kind == "int":
            low, high = STYLE_RANGES[axis]
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(low, high)
            slider.setValue(max(low, min(high, int(value))))
            layout.addWidget(slider, 1)

            readout = QLabel(str(slider.value()))
            readout.setMinimumWidth(32)
            readout.setObjectName("Subtle")
            layout.addWidget(readout)

            def changed(new_value: int, axis=axis, readout=readout) -> None:
                readout.setText(str(new_value))
                self._override(axis, new_value, immediate=False)

            slider.valueChanged.connect(changed)
            row.slider = slider
            row.readout = readout
            return row

        if kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(value))
            box.toggled.connect(lambda on, axis=axis: self._override(axis, on))
            return box

        return None

    def _topic_editor(self, title: str, placeholder: str):
        """A labelled list you can add to and remove from."""
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(4)

        heading = QLabel(title)
        heading.setStyleSheet("font-weight: 600;")
        outer.addWidget(heading)

        listing = QListWidget()
        listing.setMaximumHeight(84)
        outer.addWidget(listing)

        row = QHBoxLayout()
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        row.addWidget(field, 1)

        add = QPushButton("Add")
        row.addWidget(add)
        remove = QPushButton("Remove")
        remove.setObjectName("Quiet")
        row.addWidget(remove)
        outer.addLayout(row)

        def add_one() -> None:
            text = field.text().strip()
            if text and not listing.findItems(text, Qt.MatchFlag.MatchFixedString):
                listing.addItem(text)
                field.clear()
                self._save_interests()

        def remove_one() -> None:
            for item in listing.selectedItems():
                listing.takeItem(listing.row(item))
            self._save_interests()

        add.clicked.connect(add_one)
        field.returnPressed.connect(add_one)
        remove.clicked.connect(remove_one)
        return listing, box

    def _show_derived(self) -> None:
        """What it worked out on its own, so the guessing is visible."""
        from rose_bouquet.core.interests import derive_topics

        topics = [word for word, _ in derive_topics(self.tastes)[:12]]
        self.derived_label.setText(
            "Worked out from what you watch: " + ", ".join(topics)
            if topics else
            "Nothing worked out yet — watch a few things and this fills in."
        )

    # ── Visualiser ────────────────────────────────────────────────

    def _visualizer_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(12)

        from rose_bouquet.core import cava

        self.visualizer_on = QCheckBox("Show the visualiser")
        self.visualizer_on.setChecked(self.preferences.visualizer)
        self.visualizer_on.toggled.connect(self._on_visualizer_changed)
        form.addRow("", self.visualizer_on)

        self.shape_picker = QComboBox()
        for shape in Shape:
            self.shape_picker.addItem(shape.label, shape.value)
        self._select(self.shape_picker, self.preferences.visualizer_shape)
        self.shape_picker.currentIndexChanged.connect(self._on_visualizer_changed)
        form.addRow("Shape", self.shape_picker)

        self.fullscreen_shape_picker = QComboBox()
        for shape in Shape:
            self.fullscreen_shape_picker.addItem(shape.label, shape.value)
        self._select(self.fullscreen_shape_picker,
                     self.preferences.visualizer_fullscreen_shape)
        self.fullscreen_shape_picker.currentIndexChanged.connect(self._on_visualizer_changed)
        form.addRow("Full screen shape", self.fullscreen_shape_picker)

        # Layers. Ticking nothing leaves the single shapes above in charge,
        # which is what makes this an addition rather than a replacement —
        # nobody has to learn a new control to keep what they had.
        self.layer_list = self._layer_menu(self.preferences.visualizer_layers)
        form.addRow("Stack (bar)", self.layer_list)

        self.fullscreen_layer_list = self._layer_menu(
            self.preferences.visualizer_fullscreen_layers)
        form.addRow("Stack (full screen)", self.fullscreen_layer_list)

        # ── Scale ─────────────────────────────────────────────
        # One size per shape rather than one for everything: a radial figure
        # and a row of bars want completely different numbers, and a stack of
        # both needs each set on its own.
        self.scale_shape = QComboBox()
        for shape in Shape:
            group = "Radial" if shape.radial else "Straight"
            self.scale_shape.addItem(f"{shape.label}  ({group})", shape.value)
        self.scale_shape.currentIndexChanged.connect(self._load_scale)
        form.addRow("Scale", self.scale_shape)

        scale_row = QHBoxLayout()
        self.scale = QSlider(Qt.Orientation.Horizontal)
        self.scale.setRange(MIN_SCALE, MAX_SCALE)
        self.scale.setSingleStep(5)
        self.scale.valueChanged.connect(self._on_scale)
        scale_row.addWidget(self.scale, 1)

        self.scale_label = QLabel()
        self.scale_label.setObjectName("Subtle")
        self.scale_label.setFixedWidth(52)
        scale_row.addWidget(self.scale_label)

        reset_scale = QPushButton("Reset")
        reset_scale.setObjectName("Quiet")
        reset_scale.clicked.connect(lambda: self.scale.setValue(100))
        scale_row.addWidget(reset_scale)
        form.addRow("", scale_row)
        self._load_scale()

        layers_note = QLabel(
            "Tick several to draw them on top of each other — a turntable "
            "under radial bars, say. Nothing ticked uses the single shape."
        )
        layers_note.setObjectName("Subtle")
        layers_note.setWordWrap(True)
        form.addRow("", layers_note)

        # Intensity: a slider rather than a number, because the only way to
        # choose it is to watch it move and stop when it looks right.
        intensity_row = QHBoxLayout()
        self.intensity = QSlider(Qt.Orientation.Horizontal)
        self.intensity.setRange(20, 400)
        self.intensity.setSingleStep(5)
        self.intensity.setValue(self.preferences.visualizer_intensity)
        self.intensity.valueChanged.connect(self._on_visualizer_changed)
        intensity_row.addWidget(self.intensity, 1)

        self.intensity_label = QLabel(f"{self.preferences.visualizer_intensity}%")
        self.intensity_label.setObjectName("Subtle")
        self.intensity_label.setFixedWidth(52)
        intensity_row.addWidget(self.intensity_label)
        form.addRow("Reacts to sound", intensity_row)

        self.fps = QSpinBox()
        self.fps.setRange(cava.MIN_FRAMERATE, cava.MAX_FRAMERATE)
        self.fps.setSuffix(" fps")
        self.fps.setValue(self.preferences.visualizer_fps)
        self.fps.setToolTip(
            "Lower this if the visualiser costs too much. It sets cava's rate "
            "as well as the redraw rate, so 30 really is half the work of 60."
        )
        self.fps.valueChanged.connect(self._on_visualizer_changed)
        form.addRow("Frame rate", self.fps)

        self.alpha = QSpinBox()
        self.alpha.setRange(2, 60)
        self.alpha.setSuffix(" %")
        self.alpha.setValue(self.preferences.visualizer_alpha)
        self.alpha.valueChanged.connect(self._on_visualizer_changed)
        form.addRow("Fill opacity", self.alpha)

        # ── Colour ────────────────────────────────────────────
        self.colour_mode = QComboBox()
        for mode in ColourMode:
            self.colour_mode.addItem(mode.label, mode.value)
        self._select(self.colour_mode, self.preferences.visualizer_colour_mode)
        self.colour_mode.currentIndexChanged.connect(self._on_colour_mode)
        form.addRow("Colour", self.colour_mode)

        self.colour_motion = QComboBox()
        for motion in ColourMotion:
            self.colour_motion.addItem(motion.label, motion.value)
        self._select(self.colour_motion, self.preferences.visualizer_colour_motion)
        self.colour_motion.currentIndexChanged.connect(self._on_visualizer_changed)
        form.addRow("Colour movement", self.colour_motion)

        self.colour_count = QSpinBox()
        self.colour_count.setRange(MIN_COLOURS, MAX_COLOURS)
        self.colour_count.setValue(max(MIN_COLOURS, len(self.preferences.visualizer_colours)))
        self.colour_count.valueChanged.connect(self._on_colour_count)
        self.colour_count_row = self.colour_count
        form.addRow("How many colours", self.colour_count)

        self.swatches = QWidget()
        self.swatch_row = QHBoxLayout(self.swatches)
        self.swatch_row.setContentsMargins(0, 0, 0, 0)
        self.swatch_row.setSpacing(6)
        form.addRow("Colours", self.swatches)
        self._draw_swatches()
        self._on_colour_mode()

        self.blur = QCheckBox("Blur")
        self.blur.setToolTip("Ignored for shapes built from hard edges — "
                             "bars, blocks and dots.")
        self.blur.setChecked(self.preferences.visualizer_blur)
        self.blur.toggled.connect(self._on_visualizer_changed)
        form.addRow("", self.blur)

        note = QLabel(
            "The visualiser reads cava — waves mode, 50 bars, 60fps. A desktop "
            "bar set up the same way will draw the same numbers. It reads system "
            "audio, so it also reacts to anything else playing."
            if cava.available() else
            "cava is not installed, so the visualiser will stay flat. "
            "Install it with your package manager and it will start working."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        form.addRow(note)

        return page

    def _layer_menu(self, chosen: list) -> QPushButton:
        """A drop-down of tick boxes, one per shape.

        A menu rather than a list widget: a list of sixteen shapes either eats
        a third of the dialog or gets a scrollbar, and scrolling a box to find
        a tick box is a miserable way to choose anything.
        """
        button = QPushButton()
        button.setObjectName("Quiet")

        menu = _StayOpenMenu(button)
        button.setMenu(menu)
        button.shape_actions = []

        for shape in Shape:
            action = QAction(shape.label, menu)
            action.setCheckable(True)
            action.setChecked(shape.value in chosen)
            action.setData(shape.value)
            action.toggled.connect(
                lambda _on, b=button: (self._label_menu(b), self._on_visualizer_changed()))
            menu.addAction(action)
            button.shape_actions.append(action)

        menu.addSeparator()
        clear = QAction("None", menu)
        clear.triggered.connect(lambda: self._clear_menu(button))
        menu.addAction(clear)

        self._label_menu(button)
        return button

    @staticmethod
    def _label_menu(button: QPushButton) -> None:
        """Say what is ticked without opening the menu to find out."""
        chosen = [a.text() for a in button.shape_actions if a.isChecked()]
        if not chosen:
            button.setText("Just the shape above  ▾")
        elif len(chosen) <= 2:
            button.setText(" + ".join(chosen) + "  ▾")
        else:
            button.setText(f"{chosen[0]} + {len(chosen) - 1} more  ▾")

    def _clear_menu(self, button: QPushButton) -> None:
        for action in button.shape_actions:
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)
        self._label_menu(button)
        self._on_visualizer_changed()

    @staticmethod
    def _ticked(button: QPushButton) -> list:
        return [a.data() for a in button.shape_actions if a.isChecked()]

    def _load_scale(self) -> None:
        """Show the chosen shape's own size, without saving anything."""
        shape = self.scale_shape.currentData()
        value = int(self.preferences.visualizer_scales.get(shape, 100))

        self.scale.blockSignals(True)
        self.scale.setValue(max(MIN_SCALE, min(MAX_SCALE, value)))
        self.scale.blockSignals(False)
        self.scale_label.setText(f"{self.scale.value()}%")

    def _on_scale(self, value: int) -> None:
        shape = self.scale_shape.currentData()
        scales = dict(self.preferences.visualizer_scales)

        if value == 100:
            # Natural size is the absence of a setting, so the file does not
            # fill up with entries that say "leave this alone".
            scales.pop(shape, None)
        else:
            scales[shape] = value

        self.preferences.visualizer_scales = scales
        self.scale_label.setText(f"{value}%")
        self._on_visualizer_changed()

    def _on_colour_mode(self) -> None:
        """Only show the controls the chosen mode actually uses."""
        mode = self.colour_mode.currentData()
        several = mode == ColourMode.MULTI.value
        chooses = mode in (ColourMode.MULTI.value, ColourMode.SOLID.value)

        self.colour_count.setVisible(several)
        self.swatches.setVisible(chooses)
        self._draw_swatches()

        # This runs once while the tab is still being built, before the rest of
        # the controls exist. Applying then would read half a form.
        if hasattr(self, "blur"):
            self._on_visualizer_changed()

    def _on_colour_count(self, count: int) -> None:
        colours = list(self.preferences.visualizer_colours)
        while len(colours) < count:
            # New slots start from the palette rather than from black, so
            # adding one never makes the visualiser briefly disappear.
            colours.append(DEFAULT_COLOURS[len(colours) % len(DEFAULT_COLOURS)])
        self.preferences.visualizer_colours = colours[:count]
        self._draw_swatches()
        self._on_visualizer_changed()

    def _draw_swatches(self) -> None:
        while self.swatch_row.count():
            item = self.swatch_row.takeAt(0)
            # Held in a name rather than asked for twice: unparenting clears
            # the layout item's pointer, so the second `item.widget()` comes
            # back as None and the delete blows up.
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        mode = self.colour_mode.currentData()
        shown = (1 if mode == ColourMode.SOLID.value
                 else self.colour_count.value())

        for index in range(min(shown, len(self.preferences.visualizer_colours))):
            colour = self.preferences.visualizer_colours[index]
            button = QPushButton()
            button.setFixedSize(30, 24)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"{colour} — click to change")
            button.setStyleSheet(
                f"background-color: {colour}; border: 1px solid #00000040;"
                f" border-radius: 5px;")
            button.clicked.connect(lambda _c=False, i=index: self._pick_colour(i))
            self.swatch_row.addWidget(button)

        self.swatch_row.addStretch(1)

    def _pick_colour(self, index: int) -> None:
        from PySide6.QtWidgets import QColorDialog

        current = QColor(self.preferences.visualizer_colours[index])
        chosen = QColorDialog.getColor(current, self, "Pick a colour")
        if not chosen.isValid():
            return

        colours = list(self.preferences.visualizer_colours)
        colours[index] = chosen.name()
        self.preferences.visualizer_colours = colours
        self._draw_swatches()
        self._on_visualizer_changed()

    def _on_visualizer_changed(self) -> None:
        self.preferences.visualizer = self.visualizer_on.isChecked()
        self.preferences.visualizer_shape = self.shape_picker.currentData()
        self.preferences.visualizer_fullscreen_shape = (
            self.fullscreen_shape_picker.currentData())
        self.preferences.visualizer_alpha = self.alpha.value()
        self.preferences.visualizer_blur = self.blur.isChecked()
        self.preferences.visualizer_intensity = self.intensity.value()
        self.preferences.visualizer_fps = self.fps.value()
        self.preferences.visualizer_colour_mode = self.colour_mode.currentData()
        self.preferences.visualizer_colour_motion = self.colour_motion.currentData()
        self.preferences.visualizer_layers = self._ticked(self.layer_list)
        self.preferences.visualizer_fullscreen_layers = self._ticked(
            self.fullscreen_layer_list)

        self.intensity_label.setText(f"{self.intensity.value()}%")

        self.preferences.save()
        self.visualizer_changed.emit()

    # ── Library ───────────────────────────────────────────────────

    def _library_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Folders scanned for music"))

        self.folders = QListWidget()
        self.folders.addItems(self.preferences.folders or [str(music_dir())])
        layout.addWidget(self.folders, 1)

        row = QHBoxLayout()
        add = QPushButton("Add folder…")
        add.clicked.connect(self._add_folder)
        row.addWidget(add)

        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_folder)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)

        self.scan_on_start = QCheckBox("Look for new music when the app starts")
        self.scan_on_start.setChecked(self.preferences.scan_on_start)
        self.scan_on_start.toggled.connect(self._on_library_changed)
        layout.addWidget(self.scan_on_start)

        note = QLabel(
            "With no folders listed, your XDG music folder is used. Files are "
            "never moved or modified — the library is a cache of what was found, "
            "and rescanning rebuilds it."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        layout.addWidget(note)

        return page

    def _add_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Add a music folder", str(music_dir()))
        if chosen:
            self.folders.addItem(chosen)
            self._on_library_changed()

    def _remove_folder(self) -> None:
        for item in self.folders.selectedItems():
            self.folders.takeItem(self.folders.row(item))
        self._on_library_changed()

    def _on_library_changed(self) -> None:
        self.preferences.folders = [
            self.folders.item(row).text() for row in range(self.folders.count())
        ]
        self.preferences.scan_on_start = self.scan_on_start.isChecked()
        self.preferences.save()
        self.library_changed.emit()

    # ── Downloads ─────────────────────────────────────────────────

    def _downloads_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(12)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)

        self.download_dir = QLineEdit(self.preferences.download_dir)
        self.download_dir.setPlaceholderText("(your music folder)")
        self.download_dir.editingFinished.connect(self._on_downloads_changed)
        layout.addWidget(self.download_dir, 1)

        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_download_dir)
        layout.addWidget(browse)
        form.addRow("Save downloads to", row)

        self.download_format = QComboBox()
        for value, label in (("mp3", "MP3 — plays everywhere"),
                             ("opus", "Opus — smaller, better quality"),
                             ("m4a", "M4A — good on Apple devices"),
                             ("flac", "FLAC — lossless container")):
            self.download_format.addItem(label, value)
        self._select(self.download_format, self.preferences.download_format)
        self.download_format.currentIndexChanged.connect(self._on_downloads_changed)
        form.addRow("Format", self.download_format)

        self.use_cookies = QCheckBox("Use cookies from my browser when YouTube asks for a login")
        self.use_cookies.setChecked(self.preferences.use_browser_cookies)
        self.use_cookies.setToolTip(
            "Reads the cookie jar of a local Firefox or Waterfox profile. "
            "Nothing is sent anywhere; it is passed to yt-dlp on this machine."
        )
        self.use_cookies.toggled.connect(self._on_downloads_changed)
        form.addRow("", self.use_cookies)

        self.add_to_library = QCheckBox("Add finished downloads to the library")
        self.add_to_library.setChecked(self.preferences.add_downloads_to_library)
        self.add_to_library.toggled.connect(self._on_downloads_changed)
        form.addRow("", self.add_to_library)

        credentials = load_credentials()
        self.spotify_id = QLineEdit(credentials.get("spotify_client_id", ""))
        self.spotify_id.setPlaceholderText("optional")
        self.spotify_id.editingFinished.connect(self._on_credentials_changed)
        form.addRow("Spotify client ID", self.spotify_id)

        self.spotify_secret = QLineEdit(credentials.get("spotify_client_secret", ""))
        self.spotify_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.spotify_secret.setPlaceholderText("optional")
        self.spotify_secret.editingFinished.connect(self._on_credentials_changed)
        form.addRow("Spotify client secret", self.spotify_secret)

        note = QLabel(
            "Public Spotify playlists import without any credentials. Adding a "
            "free developer app's ID and secret makes long playlists more "
            "reliable. They are stored in a separate, owner-only file, never in "
            "your preferences.\n\n"
            "Downloading from YouTube is against YouTube's terms of service. "
            "Whether that matters for your own listening is your call to make."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        form.addRow(note)

        return page

    def _choose_download_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Where should downloads go?")
        if chosen:
            self.download_dir.setText(chosen)
            self._on_downloads_changed()

    def _on_downloads_changed(self) -> None:
        self.preferences.download_dir = self.download_dir.text().strip()
        self.preferences.download_format = self.download_format.currentData()
        self.preferences.add_downloads_to_library = self.add_to_library.isChecked()
        self.preferences.use_browser_cookies = self.use_cookies.isChecked()
        self.preferences.save()

    def _on_credentials_changed(self) -> None:
        save_credentials({
            "spotify_client_id": self.spotify_id.text().strip(),
            "spotify_client_secret": self.spotify_secret.text().strip(),
        })

    # ── Serving ───────────────────────────────────────────────────

    def _updates_row(self) -> QWidget:
        """Check for a new version, and install it.

        The command differs by how this was installed — `pipx upgrade` or a
        `git pull` — so it works that out rather than offering both and letting
        the user pick the one that cannot work.
        """
        from rose_bouquet.core import updates

        box = QWidget()
        row = QVBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)

        installed = QLabel(f"Installed: {updates.current_version()} "
                           f"({updates.install_kind()} install)")
        installed.setProperty("role", "hint")
        row.addWidget(installed)

        self.check_updates_on_start = QCheckBox("Look for updates when I open the app")
        self.check_updates_on_start.setChecked(self.preferences.check_updates_on_start)
        self.check_updates_on_start.setToolTip(
            "One request, at most once a day. It says nothing unless there is "
            "a new version.")
        self.check_updates_on_start.toggled.connect(
            lambda on: self.preferences.set_persistent("check_updates_on_start", on))
        row.addWidget(self.check_updates_on_start)

        note = QLabel("")
        note.setWordWrap(True)
        note.setProperty("role", "hint")

        buttons = QHBoxLayout()
        check = QPushButton("Check for updates")
        install = QPushButton("Update now")
        install.setEnabled(False)
        buttons.addWidget(check)
        buttons.addWidget(install)
        buttons.addStretch(1)
        row.addLayout(buttons)
        row.addWidget(note)

        def on_check() -> None:
            check.setEnabled(False)
            note.setText("Looking…")

            def work():
                return updates.latest()

            def done(release) -> None:
                check.setEnabled(True)
                if release is None:
                    note.setText("Could not check — no connection, or the release page "
                                 "is not visible from this machine.")
                    return
                if updates.is_newer(release.version, updates.current_version()):
                    note.setText(f"Version {release.version} is available.")
                    install.setEnabled(True)
                else:
                    note.setText("This is the newest version.")

            tasks.run(work, on_done=done,
                      on_error=lambda m: (check.setEnabled(True),
                                          note.setText(f"Could not check: {m}")))

        def on_install() -> None:
            install.setEnabled(False)
            note.setText("Updating…")

            def done(result) -> None:
                worked, message = result
                note.setText(message)
                install.setEnabled(not worked)

            tasks.run(updates.update, on_done=done,
                      on_error=lambda m: (install.setEnabled(True),
                                          note.setText(f"Could not update: {m}")))

        check.clicked.connect(on_check)
        install.clicked.connect(on_install)
        return box

    def _autostart_note(self) -> str:
        if not autostart.enabled():
            return ("Runs the server on its own at login — no window, no player. "
                    "The library is scanned first if there is no cached copy.")
        return f"Runs at login: {autostart.launcher()}"

    def _on_autostart_toggled(self, on: bool) -> None:
        """Write or remove the login entry, and say what happened.

        Reported rather than assumed: this writes a file into another
        program's directory, and a checkbox that silently did nothing — because
        the desktop ignores XDG autostart, or the directory is not writable —
        would be indistinguishable from one that worked.
        """
        try:
            if on:
                autostart.enable()
            else:
                autostart.disable()
        except OSError as error:
            self.server_autostart.blockSignals(True)
            self.server_autostart.setChecked(autostart.enabled())
            self.server_autostart.blockSignals(False)
            self.autostart_note.setText(f"Could not change that: {error}")
            return

        self.autostart_note.setText(self._autostart_note())

    def _server_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(12)

        config = self.preferences.server_config()

        self.server_enabled = QCheckBox("Start serving when the app opens")
        self.server_enabled.setChecked(config.enabled)
        self.server_enabled.toggled.connect(self._on_server_changed)
        form.addRow("", self.server_enabled)

        # Serving is the one thing here somebody wants running whether or not
        # they are sitting at the machine: the phone should find the library
        # after a reboot without anybody opening a music player first.
        self.server_autostart = QCheckBox("Start serving when I log in, without opening the app")
        self.server_autostart.setChecked(autostart.enabled())
        self.server_autostart.toggled.connect(self._on_autostart_toggled)
        form.addRow("", self.server_autostart)

        self.autostart_note = QLabel(self._autostart_note())
        self.autostart_note.setWordWrap(True)
        self.autostart_note.setProperty("role", "hint")
        form.addRow("", self.autostart_note)

        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(config.port or DEFAULT_PORT)
        self.port.valueChanged.connect(self._on_server_changed)
        form.addRow("Port", self.port)

        self.username = QLineEdit(config.username)
        self.username.editingFinished.connect(self._on_server_changed)
        form.addRow("Username", self.username)

        password_row = QWidget()
        layout = QHBoxLayout(password_row)
        layout.setContentsMargins(0, 0, 0, 0)

        self.password = QLineEdit(config.password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("no password — anyone on the network")
        self.password.editingFinished.connect(self._on_server_changed)
        layout.addWidget(self.password, 1)

        generate = QPushButton("Generate")
        generate.clicked.connect(self._generate_password)
        layout.addWidget(generate)
        form.addRow("Password", password_row)

        self.lan_only = QCheckBox("Only serve this machine (127.0.0.1)")
        self.lan_only.setChecked(config.host == "127.0.0.1")
        self.lan_only.toggled.connect(self._on_server_changed)
        form.addRow("", self.lan_only)

        self.remote_control = QCheckBox("Let clients control playback on this machine")
        self.remote_control.setChecked(self.preferences.remote_control)
        self.remote_control.toggled.connect(self._on_server_changed)
        form.addRow("", self.remote_control)

        note = QLabel(
            "The server speaks the Subsonic API, so the Rose Bouquet Android app "
            "and any Subsonic client — Symfonium, DSub, substreamer, Feishin — "
            "can play your library.\n\n"
            "It has no HTTPS and is meant for your own network. Do not port-forward "
            "it; put it behind a reverse proxy if you need it from outside."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        form.addRow(note)

        return page

    def _generate_password(self) -> None:
        self.password.setText(new_password())
        self.password.setEchoMode(QLineEdit.EchoMode.Normal)
        self._on_server_changed()

    def _on_server_changed(self) -> None:
        config = self.preferences.server_config()
        config.enabled = self.server_enabled.isChecked()
        config.port = self.port.value()
        config.username = self.username.text().strip() or "rose"
        config.password = self.password.text()
        config.host = "127.0.0.1" if self.lan_only.isChecked() else "0.0.0.0"

        self.preferences.set_server_config(config)
        self.preferences.remote_control = self.remote_control.isChecked()
        self.preferences.save()
        self.server_changed.emit()

    # ── About ─────────────────────────────────────────────────────

    def _about_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addStretch(1)
        layout.addWidget(rose_widget())

        for text, name in ((APP_NAME, "Heading"), (APP_TAGLINE, "Subtle"),
                           (ORGANISATION, "Subtle")):
            label = QLabel(text)
            label.setObjectName(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)

        credit = QLabel('Rose ASCII art: "rose (3/99)" by Joan G. Stark (jgs).')
        credit.setObjectName("Subtle")
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit.setWordWrap(True)
        layout.addWidget(credit)

        # Updating lived under Serving, between the autostart checkbox and the
        # server port, because that tab happened to be where the version string
        # already was. Nobody looks for "is there a new version" inside the
        # settings for hosting your library to other devices. It is here, next
        # to what version this is, and the window says so on its own besides.
        updates_row = QWidget()
        holder = QHBoxLayout(updates_row)
        holder.addStretch(1)
        holder.addWidget(self._updates_row())
        holder.addStretch(1)
        layout.addWidget(updates_row)

        layout.addStretch(2)
        return page

    # ── Applying ──────────────────────────────────────────────────

    def _on_theme_changed(self) -> None:
        self.preferences.set_persistent("theme", self.theme_picker.currentData())
        self._apply()

    def _on_style_changed(self) -> None:
        self.preferences.set_persistent("style", self.style_picker.currentData())
        self.preferences.clear_overrides()
        self._sync_axis_widgets()
        self._apply()

    def _override(self, axis: str, value, *, immediate: bool = True) -> None:
        self.preferences.override(axis, value)
        if immediate:
            self._apply()
        else:
            self._apply_timer.start()

    def _reset_overrides(self) -> None:
        self.preferences.clear_overrides()
        self._sync_axis_widgets()
        self._apply()

    def _sync_axis_widgets(self) -> None:
        for axis, widget in self.axis_widgets.items():
            value = self.preferences.value_for(axis)

            slider = getattr(widget, "slider", None)
            if slider is not None:
                low, high = STYLE_RANGES[axis]
                slider.blockSignals(True)
                slider.setValue(max(low, min(high, int(value))))
                slider.blockSignals(False)
                widget.readout.setText(str(slider.value()))
                continue

            if isinstance(widget, QCheckBox):
                widget.blockSignals(True)
                widget.setChecked(bool(value))
                widget.blockSignals(False)

    def _apply(self) -> None:
        self.appearance = self.preferences.appearance()
        self.setStyleSheet(self.appearance.stylesheet())
        self.appearance_changed.emit(self.appearance)
        self.preferences.save()
