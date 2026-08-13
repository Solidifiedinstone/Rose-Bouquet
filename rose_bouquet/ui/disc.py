"""The Disc screen — ripping a CD in, and burning one back out.

Two jobs on one screen because they are the same conversation about the same
piece of hardware: what is in the drive, and what do you want done with it.
Splitting them across two sections would mean two places to notice that there
is no drive, and two places to say so.

The screen is deliberately blunt about what is missing. A drive that is not
there, a tool that is not installed, a disc that is not audio — each says which
thing and, where it can, the command that fixes it. Ripping is the sort of
feature people try once; if the first attempt fails silently they never try
again.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QWidget,
)

from rose_bouquet.core import optical
from rose_bouquet.ui import tasks
from rose_bouquet.ui.theme import Appearance
from rose_bouquet.ui.views import ScrollingView
from rose_bouquet.ui.widgets import SectionHeading

logger = logging.getLogger(__name__)


class DiscView(ScrollingView):
    """Read an audio CD into the library, or write one from the queue."""

    status = Signal(str, str)
    #: Emitted with the folder that gained files, so the library can pick them up.
    ripped = Signal(str)
    burn_requested = Signal()
    #: Play this disc, from this drive. The disc is streamed, not ripped.
    play_disc_requested = Signal(object, object)   # Disc, device
    watch_requested = Signal(str)                  # a device or file to play as video
    #: About to use the drive for something that needs it to itself. A disc
    #: can only be read by one thing at a time, and a rip competing with
    #: playback stalls both for as long as they overlap.
    drive_needed = Signal()

    def __init__(self, appearance: Appearance, destination,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(appearance, parent)
        #: A callable returning where ripped music should land — the same
        #: folder downloads use, so a rip joins the library on the next scan.
        self.destination = destination

        self.drive: Optional[optical.Drive] = None
        self.disc: Optional[optical.Disc] = None
        self.busy = False
        self.progress_text = ""
        self.progress_percent: Optional[float] = None
        self._job = None
        self._checkboxes: dict[int, QCheckBox] = {}

        title = QLabel("Disc")
        title.setObjectName("Heading")
        self.header_layout.addWidget(title)
        self.header_layout.addStretch(1)

        self.drive_picker = QComboBox()
        self.drive_picker.setMinimumWidth(190)
        self.header_layout.addWidget(self.drive_picker)

        self.read_button = QPushButton("Read disc")
        self.read_button.setObjectName("Primary")
        self.read_button.clicked.connect(self.read_disc)
        self.header_layout.addWidget(self.read_button)

        self.refresh_drives()
        self.refresh()

    # ── Drives ────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        """Look for drives every time this screen is opened.

        Detection used to run once, when the window was built. A drive plugged
        in afterwards — or one the kernel had not finished enumerating at
        launch — stayed invisible until the app was restarted, while the screen
        cheerfully claimed no drive was attached.
        """
        super().showEvent(event)
        if not self.busy:
            self.refresh_drives()

    def refresh_drives(self) -> None:
        # Keep whatever was chosen, if it is still there: re-detecting must not
        # silently move a burn to a different drive.
        previous = self.drive_picker.currentData()

        self.drive_picker.blockSignals(True)
        self.drive_picker.clear()

        drives = optical.detect_drives()
        for drive in drives:
            self.drive_picker.addItem(drive.label, str(drive.device))

        if previous is not None:
            index = self.drive_picker.findData(previous)
            if index >= 0:
                self.drive_picker.setCurrentIndex(index)
        self.drive_picker.blockSignals(False)

        appeared = bool(drives) and previous is None
        self.drive = drives[0] if drives else None
        self.drive_picker.setVisible(bool(drives))
        self.read_button.setEnabled(bool(drives) and not self.busy)

        # The screen was drawn for a machine with no drive; it needs redrawing
        # now that there is one.
        if appeared and not self.busy:
            self.refresh()

    def _selected_device(self) -> Optional[Path]:
        data = self.drive_picker.currentData()
        return Path(data) if data else None

    # ── Reading ───────────────────────────────────────────────────

    def read_disc(self) -> None:
        self.drive_needed.emit()

        # A drive can appear between opening this screen and pressing the
        # button — a USB one takes a second or two to enumerate.
        if self.drive_picker.count() == 0:
            self.refresh_drives()

        device = self._selected_device()
        self._begin("Reading the disc…")

        def done(disc) -> None:
            self._end()
            self.disc = disc
            self.status.emit(f"{len(disc)} tracks, {disc.clock}", "success")
            self.refresh()

        def failed(message: str) -> None:
            self._end()
            self.disc = None
            self.status.emit(message.split("\n")[0], "error")
            self.refresh()

        tasks.run(lambda: optical.read_toc(device), on_done=done, on_error=failed)

    # ── Ripping ───────────────────────────────────────────────────

    def start_rip(self) -> None:
        if self.disc is None:
            return

        wanted = [number for number, box in self._checkboxes.items() if box.isChecked()]
        if not wanted:
            self.status.emit("Tick at least one track", "warning")
            return

        album = self.album_field.text().strip()
        artist = self.artist_field.text().strip()
        fmt = self.format_picker.currentData()

        folder = Path(self.destination())
        if album:
            folder = folder / optical._safe(f"{artist} - {album}" if artist else album)

        self.drive_needed.emit()
        ripper = optical.CdRipper(self._selected_device())
        self._job = ripper
        self._begin(f"Ripping {len(wanted)} tracks…")

        disc = self.disc

        def work(report):
            return ripper.rip(
                disc, folder, tracks=wanted, fmt=fmt, album=album, artist=artist,
                progress=lambda update: report(update),
            )

        def done(result) -> None:
            self._end()
            self.status.emit(result.summary, "success" if result.files else "warning")
            if result.files:
                self.ripped.emit(str(folder))

        tasks.run(work, on_progress=self._on_progress, on_done=done,
                  on_error=self._on_failed)

    def play_disc(self) -> None:
        """Play the disc.

        Streamed straight from the drive — nothing is ripped, nothing is
        written, and it starts within a second rather than after the whole
        disc has been copied somewhere.
        """
        if self.disc is not None:
            self.play_disc_requested.emit(self.disc, self._selected_device())

    # ── Video and data discs ──────────────────────────────────────

    def rip_image(self) -> None:
        """Copy a data or video disc to an .iso."""
        device = self._selected_device()
        if device is None:
            return

        target = Path(self.destination()) / "discs" / "disc.iso"
        index = 1
        while target.exists():
            index += 1
            target = target.with_name(f"disc-{index}.iso")

        self.drive_needed.emit()
        imager = optical.DiscImager(device)
        self._job = imager
        self._begin(f"Copying the disc to {target.name}…")

        def done(result) -> None:
            self._end()
            self.refresh()
            self.status.emit(result.summary, "success")

        tasks.run(lambda report: imager.rip_to_image(target, progress=report),
                  on_progress=self._on_progress, on_done=done, on_error=self._on_failed)

    def watch_disc(self) -> None:
        """Play the disc as video, on the same surface the Watch tab uses."""
        device = self._selected_device()
        if device is not None:
            self.watch_requested.emit(str(device))

    def stop(self) -> None:
        if self._job is not None:
            self._job.stop()
            self.status.emit("Stopping…", "info")

    # ── Progress plumbing ─────────────────────────────────────────

    def _begin(self, message: str) -> None:
        self.busy = True
        self.progress_text = message
        self.progress_percent = None
        self.read_button.setEnabled(False)
        self.refresh()

    def _end(self) -> None:
        self.busy = False
        self._job = None
        self.progress_text = ""
        self.progress_percent = None
        self.read_button.setEnabled(self.drive_picker.count() > 0)

    def _on_progress(self, update) -> None:
        if not isinstance(update, optical.Progress):
            return
        where = (f"Track {update.track} of {update.of_tracks} — "
                 if update.of_tracks else "")
        self.progress_text = f"{where}{update.message}"
        self.progress_percent = update.percent
        self._paint_progress()

    def _on_failed(self, message: str) -> None:
        self._end()
        # Tool-missing messages carry install instructions over several lines;
        # the banner gets the first, the screen keeps the rest.
        self.status.emit(message.split("\n")[0], "error")
        self.failure = message
        self.refresh()

    def _paint_progress(self) -> None:
        """Update the bar in place. A full rebuild sixty times a rip would
        throw away the track list and the user's ticks with it."""
        if getattr(self, "bar", None) is None:
            return
        self.bar.setFormat(self.progress_text or "")
        if self.progress_percent is None:
            self.bar.setRange(0, 0)          # indeterminate
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(self.progress_percent))

    # ── Drawing ───────────────────────────────────────────────────

    def refresh(self, *_args) -> None:
        self.clear(self.body_layout)
        self._checkboxes = {}
        self.bar = None

        absent = optical.missing("cdparanoia", "ffmpeg")
        if absent:
            self.body_layout.addWidget(self.empty_label(
                "\n\n".join(tool.message for tool in absent)))
            self.body_layout.addStretch(1)
            return

        if self.drive_picker.count() == 0:
            self.body_layout.addWidget(self.empty_label(
                "No optical drive found.\n\nPlug one in and press Read disc — "
                "USB drives are picked up without a restart."
            ))
            self.body_layout.addStretch(1)
            return

        if self.busy:
            self.body_layout.addWidget(SectionHeading("Working", self.appearance))
            self.bar = QProgressBar()
            self.bar.setTextVisible(True)
            self.body_layout.addWidget(self.bar)
            self._paint_progress()

            stop = QPushButton("Stop")
            stop.clicked.connect(self.stop)
            self.body_layout.addWidget(stop)
            self.body_layout.addStretch(1)
            return

        if self.disc is None:
            self.body_layout.addWidget(self.empty_label(
                "Put a disc in the drive and press Read disc."
            ))
            self._draw_video_actions()
            self.body_layout.addStretch(1)
            return

        self._draw_disc()
        self._draw_video_actions()
        self.body_layout.addStretch(1)

    def _draw_video_actions(self) -> None:
        """Everything that is not an audio CD.

        Shown whether or not a disc has been read, because a film disc has no
        audio TOC to read — pressing Read disc on one reports no audio CD, and
        the buttons that *do* apply to it have to be visible at that moment
        rather than behind a successful read.
        """
        self.body_layout.addWidget(SectionHeading("Films and data discs", self.appearance))

        note = QLabel(
            "Copy a disc to an .iso you can keep, or play it here.\n\n"
            "This copies the sectors a disc hands over — it does not break "
            "copy protection, so an encrypted commercial film will read as "
            "unreadable rather than as a broken file."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        self.body_layout.addWidget(note)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 6, 0, 0)

        watch = QPushButton("Watch the disc")
        watch.clicked.connect(self.watch_disc)
        row_layout.addWidget(watch)

        image = QPushButton("Copy to .iso")
        image.setToolTip("Save the disc as an image file")
        image.clicked.connect(self.rip_image)
        row_layout.addWidget(image)

        row_layout.addStretch(1)
        self.body_layout.addWidget(row)

    def _draw_disc(self) -> None:
        disc = self.disc
        self.body_layout.addWidget(SectionHeading(
            f"On the disc — {disc.clock}", self.appearance, count=len(disc)))

        details = QWidget()
        details_layout = QHBoxLayout(details)
        details_layout.setContentsMargins(0, 4, 0, 8)
        details_layout.setSpacing(8)

        # A CD carries no metadata worth the name, so the album and artist are
        # asked for rather than guessed. Blank is fine; the files are still
        # named after their track numbers.
        self.album_field = QLineEdit()
        self.album_field.setPlaceholderText("Album")
        details_layout.addWidget(self.album_field, 1)

        self.artist_field = QLineEdit()
        self.artist_field.setPlaceholderText("Artist")
        details_layout.addWidget(self.artist_field, 1)

        self.format_picker = QComboBox()
        for fmt in optical.RIP_FORMATS:
            self.format_picker.addItem(fmt.upper(), fmt)
        details_layout.addWidget(self.format_picker)

        self.body_layout.addWidget(details)

        for track in disc.tracks:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(4, 2, 4, 2)

            box = QCheckBox(f"{track.number:02d}   {track.display_title}")
            box.setChecked(True)
            self._checkboxes[track.number] = box
            row_layout.addWidget(box, 1)

            length = QLabel(track.clock)
            length.setObjectName("Subtle")
            row_layout.addWidget(length)

            self.body_layout.addWidget(row)

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 10, 0, 0)

        none_button = QPushButton("Select none")
        none_button.setObjectName("Quiet")
        none_button.clicked.connect(
            lambda: [box.setChecked(False) for box in self._checkboxes.values()])
        actions_layout.addWidget(none_button)

        all_button = QPushButton("Select all")
        all_button.setObjectName("Quiet")
        all_button.clicked.connect(
            lambda: [box.setChecked(True) for box in self._checkboxes.values()])
        actions_layout.addWidget(all_button)

        actions_layout.addStretch(1)

        listen = QPushButton("Play the disc")
        listen.setToolTip(
            "Play it now. Tracks are read one at a time and start playing as "
            "soon as the first is ready, so there is no wait for the whole disc."
        )
        listen.clicked.connect(self.play_disc)
        actions_layout.addWidget(listen)

        rip = QPushButton("Rip to library")
        rip.setObjectName("Primary")
        rip.clicked.connect(self.start_rip)
        actions_layout.addWidget(rip)

        self.body_layout.addWidget(actions)

        burn = QPushButton("Burn the queue to a disc…")
        burn.setToolTip("Write what is in the play queue to a blank CD")
        burn.clicked.connect(self.burn_requested.emit)
        self.body_layout.addWidget(burn)
