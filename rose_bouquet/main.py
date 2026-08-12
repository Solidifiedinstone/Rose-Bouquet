"""Entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rose_bouquet.ui.branding import APP_NAME, ORGANISATION


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="rose-bouquet", description=APP_NAME)
    parser.add_argument("--music-dir", type=Path, action="append",
                        help="scan this folder for music (repeatable, this run only)")
    parser.add_argument("--section", help="open on: library, albums, playlists, youtube, "
                                          "import, downloads, server")
    parser.add_argument("--serve", action="store_true",
                        help="start the network server on launch")
    parser.add_argument("--theme", help="start with this theme, ignoring the saved one")
    parser.add_argument("--style", help="start with this style, ignoring the saved one")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from rose_bouquet.ui.main_window import MainWindow
    from rose_bouquet.ui.preferences import Preferences
    from rose_bouquet.ui.theme import STYLES, THEMES

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANISATION)
    app.setDesktopFileName("rose-bouquet")

    icon = QIcon.fromTheme("rose-bouquet")
    if not icon.isNull():
        app.setWindowIcon(icon)

    # Flags override the saved preferences for this run only, and are not
    # written back: they are for trying something, not for changing a setting.
    preferences = Preferences.load()
    if args.music_dir:
        preferences.use_for_run("folders", [str(p) for p in args.music_dir])
    if args.theme in THEMES:
        preferences.use_for_run("theme", args.theme)
    if args.style in STYLES:
        preferences.use_for_run("style", args.style)
    if args.section:
        preferences.use_for_run("section", args.section)
    if args.serve:
        server = dict(preferences.server)
        server["enabled"] = True
        preferences.use_for_run("server", server)

    window = MainWindow(preferences)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
