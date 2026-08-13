"""Audio CD ripping and burning.

No optical drive is assumed — the tools are never run here. What is tested is
the part that has to be right before a drive is touched: reading what the tools
print, building the commands, and refusing the things that would waste a blank.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rose_bouquet.core import optical

# ── Reading a disc ────────────────────────────────────────────────

#: Real `cdparanoia -Q` output, which goes to stderr.
TOC = """\
cdparanoia III release 10.2 (September 11, 2008)

Table of Contents (audio tracks only):
track        length               begin        copy pre ch
===========================================================
  1.    18375 [04:05.00]        0 [00:00.00]    no   no  2
  2.    15900 [03:32.00]    18375 [04:05.00]    no   no  2
  3.    20250 [04:30.00]    34275 [07:37.00]    no   no  2
TOTAL   54525 [12:07.00]    (audio only)
"""


def test_a_table_of_contents_is_read():
    disc = optical.parse_toc(TOC)

    assert len(disc) == 3
    assert [t.number for t in disc.tracks] == [1, 2, 3]
    assert disc.tracks[0].frames == 18375
    assert disc.tracks[0].clock == "4:05"


def test_the_total_line_is_not_a_track():
    """TOTAL is right there in the table and must not become track four."""
    disc = optical.parse_toc(TOC)
    assert len(disc) == 3
    assert disc.clock == "12:07"


def test_a_data_disc_yields_no_tracks():
    assert len(optical.parse_toc("Unable to read table of contents\n")) == 0


def test_a_track_with_no_name_still_has_one():
    track = optical.DiscTrack(number=7, frames=100)
    assert track.display_title == "Track 07"


# ── Finding drives ────────────────────────────────────────────────

#: /proc/sys/dev/cdrom/info is transposed: one row per capability, one column
#: per drive.
CDROM_INFO = """\
CD-ROM information, Id: cdrom.c 3.20 2003/12/17

drive name:		sr1	sr0
drive speed:		24	48
Can write CD-R:		0	1
Can read DVD:		1	1
"""


def test_two_drives_are_read_as_two_drives():
    drives = optical.parse_cdrom_info(CDROM_INFO)

    assert [str(d.device) for d in drives] == ["/dev/sr1", "/dev/sr0"]
    # Read as rows-are-drives this comes out backwards, which is the bug the
    # transposed layout invites.
    assert drives[0].can_write is False
    assert drives[1].can_write is True


def test_no_drives_is_not_an_error():
    assert optical.parse_cdrom_info("") == []


def test_detecting_drives_never_raises():
    """Called on every visit to the disc screen, including on machines with
    no drive and no /proc entry."""
    assert isinstance(optical.detect_drives(), list)


# ── Progress ──────────────────────────────────────────────────────

def test_ripping_progress_needs_a_total_to_be_a_percentage():
    """cdparanoia reports a position but never a total."""
    line = "##: 2 [wrote] @ 1234500"

    without = optical.parse_cdparanoia_progress(line)
    assert without is not None and without.percent is None

    # cdparanoia counts 16-bit words: 18375 frames x 1176 = 21,609,000.
    with_total = optical.parse_cdparanoia_progress(line, total_frames=18375)
    assert with_total.percent == pytest.approx(5.71, abs=0.1)


def test_a_finished_track_reads_as_finished_not_as_double():
    """Counting samples instead of words made every track hit 100% halfway."""
    frames = 18375
    at_end = optical.parse_cdparanoia_progress(
        f"##: 2 [wrote] @ {frames * optical.WORDS_PER_SECTOR}", total_frames=frames)
    assert at_end.percent == pytest.approx(100.0, abs=0.01)

    halfway = optical.parse_cdparanoia_progress(
        f"##: 2 [wrote] @ {frames * optical.WORDS_PER_SECTOR // 2}", total_frames=frames)
    assert halfway.percent == pytest.approx(50.0, abs=0.1)


def test_a_negative_position_is_a_status_not_an_offset():
    update = optical.parse_cdparanoia_progress("##: -2 [wrote] @ -1", total_frames=100)
    assert update is not None
    assert update.percent is None


def test_burn_progress_is_the_ratio_the_tool_printed():
    update = optical.parse_cdrskin_progress(
        "  35 of 700 MB written (fifo 100%) [buf  99%]   4.2x.")
    assert update is not None
    assert update.percent == pytest.approx(5.0, abs=0.01)


def test_lines_that_are_not_progress_are_ignored():
    assert optical.parse_cdparanoia_progress("ripping from sector 0") is None
    assert optical.parse_cdrskin_progress("cdrskin 1.5.6 : limited edition") is None


def test_a_zero_length_burn_does_not_divide_by_zero():
    assert optical.parse_cdrskin_progress("0 of 0 MB written").percent is None


# ── Refusing to waste a blank ─────────────────────────────────────

def test_too_much_audio_for_a_disc_is_refused():
    assert optical.fits_on_a_disc(70 * 60)
    assert not optical.fits_on_a_disc(95 * 60)


def test_burning_nothing_is_an_error():
    with pytest.raises(optical.DiscError, match="Nothing to burn"):
        optical.CdBurner().burn([])


def test_burning_a_file_that_is_not_there_says_which(tmp_path):
    real = tmp_path / "present.flac"
    real.write_bytes(b"")

    with pytest.raises(optical.DiscError, match=r"missing\.flac"):
        optical.CdBurner().burn([real, tmp_path / "missing.flac"])


def test_ripping_no_tracks_is_an_error():
    disc = optical.parse_toc(TOC)
    with pytest.raises(optical.DiscError, match="No tracks selected"):
        optical.CdRipper().rip(disc, Path("/tmp"), tracks=[])


# ── Missing tools ─────────────────────────────────────────────────

def test_a_missing_tool_names_a_package_to_install(monkeypatch):
    monkeypatch.setattr(optical.shutil, "which", lambda _name: None)

    with pytest.raises(optical.MissingToolError) as caught:
        optical.require("cdparanoia")

    assert "pacman -S cdparanoia" in str(caught.value)
    assert "apt install cdparanoia" in str(caught.value)


def test_which_tools_are_missing_can_be_asked_without_raising(monkeypatch):
    monkeypatch.setattr(optical.shutil, "which", lambda name: None if name == "cdrskin" else "/usr/bin/x")
    assert [t.binary for t in optical.missing("cdparanoia", "cdrskin")] == ["cdrskin"]


# ── Filenames ─────────────────────────────────────────────────────

def test_a_track_name_becomes_a_safe_filename():
    assert optical._safe('AC/DC: Back in Black?') == "ACDC Back in Black"
    assert optical._safe("...") == "Track"
    assert optical._safe("") == "Track"


def test_output_lines_split_on_carriage_returns():
    """Every one of these tools redraws its status line in place with \\r."""
    remainder, lines = optical._split_lines(b"first\rsecond\rpartial")
    assert lines == ["first", "second"]
    assert remainder == b"partial"


# ── Video and data discs ──────────────────────────────────────────

def test_imaging_progress_reports_what_was_copied():
    """Neither tool reports a trustworthy disc size before it finishes, so
    there is a figure but never a percentage."""
    dd = optical._parse_dd_progress("12582912 bytes (13 MB, 12 MiB) copied, 2 s, 6.3 MB/s")
    assert dd is not None and dd.percent is None and "12 MB" in dd.message

    rescue = optical._parse_ddrescue_progress("rescued:   123 MB,  errsize: 0 B")
    assert rescue is not None and rescue.percent is None


def test_burning_an_image_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(optical.DiscError, match="not there"):
        optical.DiscImager(Path("/dev/sr0")).burn_image(tmp_path / "nope.iso")


def test_imaging_without_a_drive_is_refused(tmp_path):
    with pytest.raises(optical.DiscError, match="No drive"):
        optical.DiscImager().rip_to_image(tmp_path / "out.iso")


def test_the_track_callback_cannot_break_a_rip(tmp_path, monkeypatch):
    """A listener that raises must not cost the user the rest of the disc."""
    disc = optical.parse_toc(TOC)
    ripper = optical.CdRipper()

    monkeypatch.setattr(ripper, "_read_track", lambda *a, **k: a[1].write_bytes(b"x"))
    monkeypatch.setattr(ripper, "_encode", lambda *a, **k: a[1].write_bytes(b"x"))
    monkeypatch.setattr(optical, "require", lambda _name: "/bin/true")

    def explode(_number, _path):
        raise RuntimeError("listener is broken")

    result = ripper.rip(disc, tmp_path, fmt="flac", on_track=explode)
    assert len(result.files) == 3


def test_a_drive_that_appears_later_is_found(monkeypatch, tmp_path):
    """Detection has to be repeatable, not a one-off at startup.

    A drive plugged in after launch — or one the kernel had not finished
    enumerating — must not stay invisible until the app is restarted.
    """
    fake = tmp_path / "sr0"
    present = {"yes": False}

    monkeypatch.setattr(optical, "CDROM_INFO", tmp_path / "no-such-info")
    monkeypatch.setattr(optical, "DEVICE_GLOBS", (str(tmp_path / "sr[0-9]*"),))

    assert optical.detect_drives() == []

    fake.write_bytes(b"")
    present["yes"] = True
    found = optical.detect_drives()
    assert [d.device.name for d in found] == ["sr0"]
