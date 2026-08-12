"""The first launch: find the music before showing an empty library.

Opening a music player to a blank list and a Settings button is the worst
possible first impression — it looks broken, and the fix is buried. So the very
first run asks one question, with the answer already filled in from the XDG
music folder, and scans as soon as it is confirmed.

Shown exactly once: it keys off the preferences file not existing yet, not off
the library being empty, so someone who genuinely has no music and dismissed it
is not asked again every launch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rose_bouquet.core.library import AUDIO_SUFFIXES, music_dir
from rose_bouquet.ui.branding import APP_NAME, APP_TAGLINE, rose_widget
from rose_bouquet.ui.theme import Appearance


def count_audio(folder: Path, ceiling: int = 500) -> int:
    """A quick look for audio files, so the dialog can say what it found.

    Stops at the ceiling rather than walking a 50,000-file library twice: the
    exact number does not matter, "there is music here" does.
    """
    found = 0
    try:
        for path in folder.rglob("*"):
            if path.suffix.lower() in AUDIO_SUFFIXES:
                found += 1
                if found >= ceiling:
                    break
    except (OSError, PermissionError):
        return found
    return found


class FirstRunDialog(QDialog):
    """One question, answered in advance."""

    def __init__(self, appearance: Appearance, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.appearance = appearance
        self.folder = music_dir()

        self.setWindowTitle(f"Welcome to {APP_NAME}")
        self.setModal(True)
        self.resize(520, 480)
        self.setStyleSheet(appearance.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        layout.addWidget(rose_widget())

        for text, name in ((APP_NAME, "Heading"), (APP_TAGLINE, "Subtle")):
            label = QLabel(text)
            label.setObjectName(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)

        layout.addSpacing(8)

        question = QLabel("Where do you keep your music?")
        question.setStyleSheet(
            f"color: {appearance.theme.text}; font-weight: 700;"
            f" font-size: {appearance.style.font_size + 2}px;"
        )
        layout.addWidget(question)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        self.field = QLineEdit(str(self.folder))
        self.field.textChanged.connect(self._on_folder_changed)
        row_layout.addWidget(self.field, 1)

        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row_layout.addWidget(browse)
        layout.addWidget(row)

        self.found = QLabel()
        self.found.setObjectName("Subtle")
        self.found.setWordWrap(True)
        layout.addWidget(self.found)

        self.watch = QCheckBox("Look for new music each time Rose Bouquet opens")
        self.watch.setChecked(True)
        layout.addWidget(self.watch)

        note = QLabel(
            "Files are never moved or changed — this only tells Rose Bouquet "
            "where to look. You can add more folders later in Settings, and "
            "anything you download lands in its own folder regardless."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        skip = QPushButton("Skip for now")
        skip.clicked.connect(self.reject)
        buttons.addWidget(skip)

        self.confirm = QPushButton("Scan this folder")
        self.confirm.setObjectName("Primary")
        self.confirm.setDefault(True)
        self.confirm.clicked.connect(self.accept)
        buttons.addWidget(self.confirm)

        layout.addLayout(buttons)

        self._on_folder_changed(str(self.folder))

    # ── Behaviour ─────────────────────────────────────────────────

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Where do you keep your music?", str(self.folder)
        )
        if chosen:
            self.field.setText(chosen)

    def _on_folder_changed(self, text: str) -> None:
        folder = Path(text).expanduser()
        self.folder = folder

        if not folder.is_dir():
            self.found.setText("That folder does not exist yet.")
            self.confirm.setEnabled(False)
            return

        self.confirm.setEnabled(True)
        count = count_audio(folder)

        if count >= 500:
            self.found.setText("Found 500+ audio files here.")
        elif count:
            self.found.setText(f"Found {count} audio file{'' if count == 1 else 's'} here.")
        else:
            self.found.setText(
                "No audio files found in there — you can still use it, and "
                "anything you download will show up."
            )

    @property
    def scan_on_start(self) -> bool:
        return self.watch.isChecked()
