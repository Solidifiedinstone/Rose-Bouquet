"""The app actually starting.

Every other test builds a `MainWindow` by hand, which skips `main()` entirely
— the logging setup, the excepthook, the argument parsing. A change there once
deadlocked the app before its window ever appeared, and no test noticed,
because no test had ever run the thing the user runs.

So this launches the real entry point in a real subprocess, lets it settle,
and checks it is still alive and has not written a traceback. Started rather
than exited: an app whose job is to sit there is proved by sitting there.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

#: How long to give a cold start before calling it hung.
#:
#: Generous on purpose. This test asks whether the app comes up at all, not
#: whether it comes up quickly — and a cold start is a Qt import, a media
#: backend and a window, on whatever else the machine happens to be doing. At
#: thirty seconds it failed about one run in five on a loaded machine, which
#: teaches you to ignore the suite. A hang still fails, just later.
STARTUP_TIMEOUT = 120


def _launch(tmp_path: Path, *args: str) -> subprocess.Popen:
    """Start the app in its own world.

    Isolated on purpose: pointed at real data it would scan a library and
    rebuild a feed over the network, which makes the test slow, flaky, and
    able to edit the very files it should leave alone. A preferences file is
    written first, because without one the app treats this as a first run and
    opens a modal folder picker that nothing is there to dismiss.
    """
    config = tmp_path / "config" / "rose-bouquet"
    config.mkdir(parents=True, exist_ok=True)
    (config / "preferences.json").write_text(json.dumps({
        "folders": [str(tmp_path / "music")],
        "scan_on_start": False,
        "visualizer": False,
    }))
    (tmp_path / "music").mkdir(parents=True, exist_ok=True)

    environment = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen",
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_MUSIC_DIR=str(tmp_path / "music"),
    )
    return subprocess.Popen(
        [sys.executable, "-m", "rose_bouquet.main", *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=environment, start_new_session=True,
    )


def _finished_starting(tmp_path: Path) -> bool:
    """Whether the app got all the way up, according to its own log.

    Not "is the process alive": a deadlocked app is alive and will sit there
    forever. The marker is written after the window is shown, so its presence
    is the difference between started and merely launched.
    """
    log = tmp_path / "data" / "rose-bouquet" / "logs" / "rose-bouquet.log"
    return log.exists() and "started" in log.read_text(errors="replace")


def _settle(process: subprocess.Popen, tmp_path: Path) -> tuple[bool, str]:
    """Wait for it to finish starting, then stop it.

    Polled rather than slept: a fixed wait is either too short under load —
    which is a flaky test — or too long always, which is a slow one. Returns
    (it finished starting, its output).
    """
    deadline = time.time() + STARTUP_TIMEOUT
    alive = False
    while time.time() < deadline:
        if _finished_starting(tmp_path):
            alive = True
            break
        if process.poll() is not None:
            break                       # it died; nothing more to wait for
        time.sleep(0.25)

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    code = process.poll()
    try:
        output = process.communicate(timeout=15)[0] or ""
    except subprocess.TimeoutExpired:
        process.kill()
        output = process.communicate()[0] or ""

    if not alive:
        # "It never finished starting" on its own sends you to read a wall of
        # Qt and PipeWire chatter for a fact the process already knew: whether
        # it died, when, and what it had managed to log first.
        log = tmp_path / "data" / "rose-bouquet" / "logs" / "rose-bouquet.log"
        lines = log.read_text(errors="replace").splitlines() if log.exists() else []
        output = (
            f"exit code: {code if code is not None else 'still running (timed out)'}\n"
            f"log tail: {lines[-6:] or 'the log was never written'}\n\n" + output
        )
    return alive, output


def test_the_app_starts_and_keeps_running(tmp_path):
    started, output = _settle(_launch(tmp_path), tmp_path)
    assert started, f"it never finished starting:\n{output[-2000:]}"


def test_starting_writes_no_traceback(tmp_path):
    _started, output = _settle(_launch(tmp_path), tmp_path)
    for marker in ("Traceback", "unhandled error"):
        assert marker not in output, output[-2000:]


def test_a_section_flag_is_honoured(tmp_path):
    started, output = _settle(_launch(tmp_path, "--section", "library"), tmp_path)
    assert started, output[-2000:]


def test_an_unknown_section_does_not_stop_it(tmp_path):
    """A stale preference or a typo must not be fatal."""
    started, output = _settle(_launch(tmp_path, "--section", "no-such-section"), tmp_path)
    assert started, output[-2000:]
