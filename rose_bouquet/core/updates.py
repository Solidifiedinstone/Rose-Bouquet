"""Checking for a new version, and updating in place.

Installed with pipx or from a clone, so an update is `pipx upgrade` or a
`git pull`. Both are one command somebody has to remember, in a terminal they
may not have open — this is that command behind a button, and it reports what
actually happened rather than claiming success.

Nothing is installed without being asked: the caller checks first, shows what
is available, and only then runs the upgrade.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

RELEASES = "https://api.github.com/repos/Solidifiedinstone/Rose-Bouquet/releases/latest"

#: Long enough for a slow connection, short enough that a hung request does not
#: look like a frozen settings window.
TIMEOUT = 10


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str


def current_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("rose-bouquet")
    except PackageNotFoundError:
        return "0.0.0"


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a later version than `current`.

    Compared as numbers, field by field. A string comparison calls 0.1.10 older
    than 0.1.9, which breaks exactly when releases start mattering.
    """
    def parts(value: str) -> list[int]:
        out = []
        for chunk in value.strip().lstrip("v").replace("-", ".").split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            if digits:
                out.append(int(digits))
        return out

    new, old = parts(candidate), parts(current)
    for index in range(max(len(new), len(old))):
        a = new[index] if index < len(new) else 0
        b = old[index] if index < len(old) else 0
        if a != b:
            return a > b
    return False


def latest() -> Release | None:
    """The newest published release, or None if that cannot be established.

    None rather than an exception: no connection, or a private repository this
    machine cannot see, is not a crash.
    """
    request = urllib.request.Request(
        RELEASES, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None

    tag = str(payload.get("tag_name") or "").lstrip("v")
    if not tag:
        return None
    return Release(tag, str(payload.get("body") or ""), str(payload.get("html_url") or ""))


def install_kind() -> str:
    """How this copy was installed: 'pipx', 'clone', or 'unknown'.

    They update differently, and guessing wrong means running a command that
    cannot work — `pipx upgrade` on a clone, or `git pull` on a pipx install.
    """
    root = Path(__file__).resolve().parent.parent.parent
    if (root / ".git").exists():
        return "clone"
    if "pipx" in sys.executable or "pipx" in str(Path(sys.prefix)):
        return "pipx"
    return "unknown"


def update() -> tuple[bool, str]:
    """Run the upgrade. Returns (it worked, what to tell the user).

    The command's own output is returned on failure rather than a generic
    message, because "it did not work" is not something anybody can act on.
    """
    kind = install_kind()

    if kind == "pipx":
        pipx = shutil.which("pipx")
        if not pipx:
            return False, "pipx is not on PATH any more — run: pipx upgrade rose-bouquet"
        command = [pipx, "upgrade", "rose-bouquet"]
    elif kind == "clone":
        git = shutil.which("git")
        if not git:
            return False, "git is not installed, so this clone cannot pull."
        command = [git, "-C", str(Path(__file__).resolve().parent.parent.parent), "pull",
                   "--ff-only"]
    else:
        return False, ("Cannot tell how this was installed. Update with "
                       "`pipx upgrade rose-bouquet`, or `git pull` in your clone.")

    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"Could not run the update: {error}"

    output = (finished.stdout + finished.stderr).strip()
    if finished.returncode != 0:
        return False, output or "The update command failed."

    if "Already up to date" in output or "already at latest version" in output.lower():
        return True, "Already up to date."
    return True, f"{output}\n\nRestart Rose Bouquet to use the new version."
