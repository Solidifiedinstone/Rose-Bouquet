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
    parser.add_argument("--section", help="open on: watch, library, albums, playlists, "
                                          "import, downloads, disc, server")
    parser.add_argument("--serve", action="store_true",
                        help="start the network server on launch")
    parser.add_argument("--serve-only", action="store_true",
                        help="serve the library with no window, and nothing else")
    parser.add_argument("--theme", help="start with this theme, ignoring the saved one")
    parser.add_argument("--style", help="start with this style, ignoring the saved one")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def _icon():
    """The app icon, from the icon theme or straight off disk.

    Qt's `fromTheme` needs an icon theme configured, and plenty of Wayland
    sessions do not give Qt one — Hyprland included. When that lookup comes back
    empty the icon is loaded from where the installer put it, so the window and
    the taskbar get the right rose either way.
    """
    from pathlib import Path as _Path

    from PySide6.QtGui import QIcon

    themed = QIcon.fromTheme("rose-bouquet")
    if not themed.isNull():
        return themed

    import os

    base = os.environ.get("XDG_DATA_HOME") or str(_Path.home() / ".local" / "share")
    icons = _Path(base) / "icons" / "hicolor"

    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        candidate = icons / f"{size}x{size}" / "apps" / "rose-bouquet.png"
        if candidate.exists():
            icon.addFile(str(candidate))

    scalable = icons / "scalable" / "apps" / "rose-bouquet.svg"
    if icon.isNull() and scalable.exists():
        icon.addFile(str(scalable))

    return icon


def _log_to_file(level: int) -> None:
    """Keep a rolling log on disk as well as on the terminal.

    Most people run this from a launcher, where stdout goes nowhere anybody
    can read it — so when something misbehaves there is no record of it at all
    and the only evidence is what the user managed to describe. One capped file
    costs nothing and turns "it did nothing" into something answerable.
    """
    import logging.handlers

    try:
        from rose_bouquet.core.library import data_dir

        folder = data_dir() / "logs"
        folder.mkdir(parents=True, exist_ok=True)

        handler = logging.handlers.RotatingFileHandler(
            folder / "rose-bouquet.log", maxBytes=512_000, backupCount=2, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
        handler.setLevel(level)

        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(min(root.level, level))
    except OSError:
        # A read-only home is not a reason to refuse to start.
        pass


def _log_crashes() -> None:
    """Send anything that escapes to the log as well as the terminal.

    Qt keeps running after an exception in a callback, which is merciful but
    silent: the traceback goes to a terminal nobody launched the app from, and
    the only evidence left is a user saying it did something odd. A shipped
    app has to leave a record of its own bugs.
    """
    import sys

    previous = sys.excepthook

    def hook(kind, value, traceback) -> None:
        logging.getLogger("rose_bouquet").critical(
            "unhandled error", exc_info=(kind, value, traceback))
        previous(kind, value, traceback)

    sys.excepthook = hook

    # Qt's own messages are deliberately *not* routed here. Installing a
    # `qInstallMessageHandler` that logs through Python deadlocked the app on
    # startup: Qt emits messages from its own threads while holding internal
    # locks, Python's logging takes locks of its own, and the two met during
    # multimedia initialisation. The window never appeared. Catching Python's
    # own escapes — which is what was actually needed — costs none of that.


def _serve_only(args) -> int:
    """Serve the library, with no interface at all.

    What the login entry runs. No QApplication is created: a headless server
    has no windows, and constructing a GUI toolkit to avoid using it would tie
    this to a graphical session it does not need — the point is that it comes
    up after a reboot on its own.

    It blocks until killed, which is what an autostarted service should do.
    """
    import signal
    import threading

    from rose_bouquet.core.library import Library
    from rose_bouquet.core.server import MusicServer
    from rose_bouquet.ui.preferences import Preferences

    log = logging.getLogger("rose_bouquet")

    preferences = Preferences.load()
    folders = [Path(f) for f in (args.music_dir or preferences.folders)]

    library = Library.load()
    if not library.tracks:
        # A cold start after a reboot has no cached library to serve, and a
        # server with nothing in it looks broken from the phone.
        log.info("no cached library; scanning %d folder(s)", len(folders))
        library.scan(folders)
        library.save()

    config = preferences.server_config()
    config.enabled = True
    server = MusicServer(library, config)
    server.start()

    log.info("serving %d tracks on port %d", len(library.tracks), config.port)
    print(f"Serving {len(library.tracks)} tracks on port {config.port}. Ctrl-C to stop.")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        stop.wait()
    finally:
        server.stop()
        log.info("stopped serving")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    _log_to_file(logging.DEBUG if args.verbose else logging.INFO)
    _log_crashes()

    if args.serve_only:
        return _serve_only(args)

    from PySide6.QtWidgets import QApplication

    from rose_bouquet.ui.main_window import MainWindow
    from rose_bouquet.ui.preferences import Preferences
    from rose_bouquet.ui.theme import STYLES, THEMES

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANISATION)
    app.setDesktopFileName("rose-bouquet")

    app.setWindowIcon(_icon())

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

    # A definite marker that startup finished. Worth a line on its own: a
    # hung app is still a running app, so "the process is alive" proves
    # nothing — this is the difference between started and merely launched.
    logging.getLogger("rose_bouquet").info("started")

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
