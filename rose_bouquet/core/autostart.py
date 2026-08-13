"""Starting the server when you log in, without opening the app.

Serving is the one thing here somebody wants running whether or not they are
sitting at the machine — the phone should find the library after a reboot
without anybody having to open a music player on the desktop first.

A desktop entry in `~/.config/autostart` rather than a systemd unit or an
OpenRC service, for three reasons: it needs no root, it works the same on every
desktop that follows the XDG spec, and a *user* session is where it belongs —
the server reads a music folder in the user's home, so starting it before
anybody has logged in would be starting it for nobody.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: One file, named so it is obvious what it is when found in a settings screen.
ENTRY_NAME = "rose-bouquet-serve.desktop"


def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def entry_path() -> Path:
    return autostart_dir() / ENTRY_NAME


def launcher() -> str:
    """The command that starts a headless server.

    The absolute path to whatever `rose-bouquet` is on the PATH right now, and
    the interpreter that is running as a fallback. Desktop entries do not
    inherit a shell's PATH, so a bare command name works when tested from a
    terminal and then silently fails at login — the same trap the app's own
    desktop entry documents.
    """
    found = shutil.which("rose-bouquet")
    if found:
        return f"{found} --serve-only"
    return f"{sys.executable} -m rose_bouquet.main --serve-only"


def enabled() -> bool:
    return entry_path().exists()


def enable() -> Path:
    """Write the entry. Returns where it went, so the caller can say."""
    autostart_dir().mkdir(parents=True, exist_ok=True)
    entry_path().write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Rose Bouquet — Serve\n"
        "Comment=Share this library on the local network\n"
        f"Exec={launcher()}\n"
        "Icon=rose-bouquet\n"
        "Terminal=false\n"
        # No window, so it does not want to appear in a dock or a task switcher.
        "NoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    return entry_path()


def disable() -> None:
    entry_path().unlink(missing_ok=True)
