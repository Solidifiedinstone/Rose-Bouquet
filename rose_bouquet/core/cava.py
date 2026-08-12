"""Audio levels from cava, for the visualiser.

Deliberately the same source the rest of Gavin's desktop uses: cava reading
PipeWire, in waves mode, 50 bars, 60fps, raw ASCII on stdout — the exact
settings in `~/.config/quickshell/ii/scripts/cava/raw_output_config.txt`. The
visualiser in the player and the one in the bar are then drawing the same
numbers, and a track looks the same in both.

Reading system audio rather than the app's own buffer has a nice property: it
visualises whatever is actually playing, so it still works for audio Rose Bouquet
is streaming to another device, or for anything else on the machine.

If cava is not installed this degrades to silence — no bars, no error, no
crash. A visualiser is decoration; it must never be the reason a music player
will not start.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Matches the Quickshell config so both visualisers agree.
BARS = 50
FRAMERATE = 60
NOISE_REDUCTION = 20

#: cava's ASCII raw output is 0–1000 per bar by default.
MAX_VALUE = 1000

CONFIG = """\
# Written by Rose Bouquet. Mirrors the Quickshell raw-output config so the
# player's visualiser and the desktop bar's are drawing the same numbers.
[general]
mode = waves
framerate = {framerate}
autosens = 1
bars = {bars}

[input]
method = pipewire
source = auto

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = {maximum}
channels = mono
mono_option = average

[smoothing]
noise_reduction = {noise}
"""


def available() -> bool:
    return shutil.which("cava") is not None


def write_config(path: Optional[Path] = None) -> Path:
    """Write the cava config this app drives, and return where it went."""
    if path is None:
        folder = Path(tempfile.gettempdir()) / "rose-bouquet"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "cava.conf"

    path.write_text(
        CONFIG.format(
            framerate=FRAMERATE, bars=BARS,
            maximum=MAX_VALUE, noise=NOISE_REDUCTION,
        ),
        encoding="utf-8",
    )
    return path


def parse_frame(line: str, bars: int = BARS) -> list[float]:
    """One line of cava ASCII output as a list of 0.0–1.0 levels.

    cava emits `12;340;89;…` per frame. A short or malformed frame is padded
    rather than rejected: a dropped sample should cost one frame of one bar,
    not the whole visualiser.
    """
    values: list[float] = []
    for chunk in line.strip().strip(";").split(";"):
        if not chunk:
            continue
        try:
            values.append(min(1.0, max(0.0, int(chunk) / MAX_VALUE)))
        except ValueError:
            values.append(0.0)

    if len(values) < bars:
        values.extend([0.0] * (bars - len(values)))
    return values[:bars]


def smooth(values: list[float], window: int = 2) -> list[float]:
    """A moving average over neighbouring bars.

    The same smoothing the Quickshell visualiser applies before drawing, with
    the same window, so the curve has the same character rather than merely the
    same data.
    """
    count = len(values)
    if count < 2 or window <= 0:
        return list(values)

    smoothed = []
    for index in range(count):
        low = max(0, index - window)
        high = min(count - 1, index + window)
        span = values[low:high + 1]
        smoothed.append(sum(span) / len(span))
    return smoothed


def start(config: Optional[Path] = None) -> Optional[subprocess.Popen]:
    """Start cava, or return None if it is not available.

    stderr is discarded: cava is chatty about audio devices, and none of it is
    actionable from inside a music player.
    """
    if not available():
        logger.info("cava is not installed — the visualiser will stay flat")
        return None

    path = write_config(config)
    try:
        return subprocess.Popen(
            ["cava", "-p", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        logger.warning("could not start cava: %s", exc)
        return None
