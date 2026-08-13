"""Audio levels from cava, for the visualiser.

cava reading the system audio, in waves mode, 50 bars, 60fps, raw ASCII on
stdout. These are the settings desktop bars commonly use for their own cava
visualisers, so a bar configured the same way and this player end up drawing
the same numbers and a track looks the same in both.

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

#: The common defaults for a bar's cava visualiser, so both agree.
BARS = 50
FRAMERATE = 60
NOISE_REDUCTION = 20

#: cava's ASCII raw output is 0–1000 per bar by default.
MAX_VALUE = 1000

CONFIG = """\
# Written by Rose Bouquet. Mirrors the usual raw-output config so the
# player's visualiser and a desktop bar's can draw the same numbers.
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


#: cava will accept a very wide range; these are the bounds worth offering.
#: Below 10 the motion reads as stuttering rather than slow, and above 144
#: nothing on a normal display can show the difference.
MIN_FRAMERATE = 10
MAX_FRAMERATE = 144


def clamp_framerate(framerate: int) -> int:
    try:
        return max(MIN_FRAMERATE, min(MAX_FRAMERATE, int(framerate)))
    except (TypeError, ValueError):
        return FRAMERATE


def write_config(path: Optional[Path] = None, *, framerate: int = FRAMERATE) -> Path:
    """Write the cava config this app drives, and return where it went."""
    if path is None:
        folder = Path(tempfile.gettempdir()) / "rose-bouquet"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "cava.conf"

    path.write_text(
        CONFIG.format(
            framerate=clamp_framerate(framerate), bars=BARS,
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

    The same smoothing a bar's visualiser applies before drawing, with
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


def start(config: Optional[Path] = None, *,
          framerate: int = FRAMERATE) -> Optional[subprocess.Popen]:
    """Start cava, or return None if it is not available.

    The frame rate is cava's, not just the widget's: asking cava for 30 frames
    a second costs half the CPU of asking for 60 and then dropping every other
    one. On a machine that cannot keep up, that is the difference that matters.

    stderr is discarded: cava is chatty about audio devices, and none of it is
    actionable from inside a music player.
    """
    if not available():
        logger.info("cava is not installed — the visualiser will stay flat")
        return None

    path = write_config(config, framerate=framerate)
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
