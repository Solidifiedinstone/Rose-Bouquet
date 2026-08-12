"""Settings: appearance, library, visualiser, downloads, serving, and about.

Everything applies live, as in the other Rose apps — dragging a slider repaints
the window behind the dialog rather than waiting for an OK button.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
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
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core.library import music_dir
from rose_bouquet.core.server import DEFAULT_PORT, new_password
from rose_bouquet.ui.branding import APP_NAME, APP_TAGLINE, ORGANISATION, rose_widget
from rose_bouquet.ui.preferences import (
    STYLE_AXES,
    STYLE_RANGES,
    Preferences,
    load_credentials,
    save_credentials,
)
from rose_bouquet.ui.theme import Appearance, list_style_names, list_theme_names
from rose_bouquet.ui.visualizer import Shape

APPLY_DELAY_MS = 40


class SettingsDialog(QDialog):
    """Everything configurable, applied as it changes."""

    appearance_changed = Signal(Appearance)
    library_changed = Signal()
    visualizer_changed = Signal()
    server_changed = Signal()

    def __init__(self, preferences: Preferences, parent: Optional[QWidget] = None) -> None:
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

        self.alpha = QSpinBox()
        self.alpha.setRange(2, 60)
        self.alpha.setSuffix(" %")
        self.alpha.setValue(self.preferences.visualizer_alpha)
        self.alpha.valueChanged.connect(self._on_visualizer_changed)
        form.addRow("Fill opacity", self.alpha)

        self.blur = QCheckBox("Blur it, like the one in the bar")
        self.blur.setChecked(self.preferences.visualizer_blur)
        self.blur.toggled.connect(self._on_visualizer_changed)
        form.addRow("", self.blur)

        note = QLabel(
            "The visualiser reads cava with the same settings as your Quickshell "
            "config — waves mode, 50 bars, 60fps — so the player and the bar draw "
            "the same numbers. It reads system audio, so it also reacts to "
            "anything else playing."
            if cava.available() else
            "cava is not installed, so the visualiser will stay flat. "
            "Install it with your package manager and it will start working."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        form.addRow(note)

        return page

    def _on_visualizer_changed(self) -> None:
        self.preferences.visualizer = self.visualizer_on.isChecked()
        self.preferences.visualizer_shape = self.shape_picker.currentData()
        self.preferences.visualizer_alpha = self.alpha.value()
        self.preferences.visualizer_blur = self.blur.isChecked()
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

    def _server_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(12)

        config = self.preferences.server_config()

        self.server_enabled = QCheckBox("Start serving when the app opens")
        self.server_enabled.setChecked(config.enabled)
        self.server_enabled.toggled.connect(self._on_server_changed)
        form.addRow("", self.server_enabled)

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
