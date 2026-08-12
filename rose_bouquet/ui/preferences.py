"""Interface and behaviour preferences that survive a restart.

Same shape as the other Rose apps: only the *names* of a theme and style are
stored, plus whatever individual axes were overridden, so a built-in theme that
improves in a later release improves for existing users while their own
adjustments survive.

Credentials are the exception and live in their own file with tight permissions.
Sharing a preferences file — to copy a theme to another machine, or to paste
into a bug report — must never hand over a Spotify secret or a server password
with it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from rose_bouquet.core.server import ServerConfig
from rose_bouquet.ui.theme import (
    DEFAULT_STYLE,
    DEFAULT_THEME,
    STYLES,
    THEMES,
    Appearance,
    get_style,
    get_theme,
)
from rose_bouquet.ui.visualizer import Shape

logger = logging.getLogger(__name__)


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "rose-bouquet"


DEFAULT_PATH = config_dir() / "preferences.json"
CREDENTIALS_PATH = config_dir() / "credentials.json"

STYLE_AXES: dict[str, tuple[str, str]] = {
    "radius": ("Corners", "int"),
    "spacing": ("Spacing", "int"),
    "padding": ("Padding", "int"),
    "font_size": ("Interface text", "int"),
    "heading_size": ("Heading size", "int"),
    "border_width": ("Border weight", "int"),
    "elevated_panels": ("Raised panels", "bool"),
}

STYLE_RANGES: dict[str, tuple[int, int]] = {
    "radius": (0, 40),
    "spacing": (4, 40),
    "padding": (2, 24),
    "font_size": (9, 24),
    "heading_size": (14, 44),
    "border_width": (0, 4),
}


# ── Credentials ───────────────────────────────────────────────────

def load_credentials(path: Optional[Path] = None) -> dict:
    """Secrets, from their own file. The environment wins where it is set."""
    data: dict[str, Any] = {}
    try:
        loaded = json.loads(Path(path or CREDENTIALS_PATH).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        pass

    # Anyone who would rather not write a secret to disk at all can export
    # these instead and never touch the settings fields.
    for key, variable in (
        ("spotify_client_id", "SPOTIFY_CLIENT_ID"),
        ("spotify_client_secret", "SPOTIFY_CLIENT_SECRET"),
        ("server_password", "ROSE_MUSIC_PASSWORD"),
    ):
        from_env = (os.environ.get(variable) or "").strip()
        if from_env:
            data[key] = from_env

    return data


def save_credentials(values: dict, path: Optional[Path] = None) -> None:
    path = Path(path or CREDENTIALS_PATH)
    data = load_credentials(path)
    data.update({k: v for k, v in values.items() if v is not None})
    data = {k: v for k, v in data.items() if v != ""}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)          # readable only by its owner
        temporary.replace(path)
    except OSError as exc:
        logger.warning("could not save credentials to %s: %s", path, exc)


@dataclass
class Preferences:
    """Everything the user chose about how Rose Bouquet looks and behaves."""

    theme: str = DEFAULT_THEME
    style: str = DEFAULT_STYLE
    style_overrides: dict[str, Any] = field(default_factory=dict)

    section: str = "library"
    window_size: tuple[int, int] = (1180, 760)
    sidebar_width: int = 210

    # ── Music ─────────────────────────────────────────────────────

    #: Folders scanned for music. Empty means the XDG music folder.
    folders: list[str] = field(default_factory=list)
    #: Where downloads land. Empty means the data folder's `downloads`.
    download_dir: str = ""
    download_format: str = "mp3"
    #: Rescan on startup. Off for anyone with a very large library on a spinner.
    scan_on_start: bool = True
    #: Read cookies from a local browser profile when YouTube demands a login.
    #: Off by default — an app should not touch a cookie jar unasked.
    use_browser_cookies: bool = False
    #: Add downloads to the library as soon as they finish.
    add_downloads_to_library: bool = True

    # ── Visualiser ────────────────────────────────────────────────

    visualizer: bool = True
    visualizer_shape: str = Shape.WAVE.value
    #: Fill opacity. The Quickshell one uses 0.15; this is that in percent.
    visualizer_alpha: int = 15
    visualizer_blur: bool = True

    # ── Server ────────────────────────────────────────────────────

    server: dict = field(default_factory=dict)
    #: Let clients on the network control what this machine is playing.
    remote_control: bool = False

    volume: float = 0.8

    transient: dict[str, Any] = field(default_factory=dict)

    #: True when there was no preferences file to read — i.e. this is the first
    #: launch. Not saved; it is a fact about this run, not a setting.
    first_run: bool = False

    # ── Derived ───────────────────────────────────────────────────

    def appearance(self) -> Appearance:
        return Appearance(
            theme=get_theme(self.theme),
            style=get_style(self.style).with_overrides(**self.style_overrides),
        )

    def server_config(self) -> ServerConfig:
        config = ServerConfig.from_dict(self.server)
        # The password lives with the other secrets, not in preferences.
        config.password = load_credentials().get("server_password", "")
        return config

    def set_server_config(self, config: ServerConfig) -> None:
        data = config.to_dict()
        password = data.pop("password", "")
        self.server = data
        save_credentials({"server_password": password})

    def downloads_path(self):
        """Where downloads go.

        An explicit setting wins; otherwise they land in the first music folder,
        so anything downloaded joins the library on the next scan instead of
        hiding in an application data directory nobody thinks to look in.
        """
        from pathlib import Path

        from rose_bouquet.core.library import data_dir

        if self.download_dir.strip():
            return Path(self.download_dir).expanduser()
        if self.folders:
            return Path(self.folders[0]).expanduser()
        return data_dir() / "downloads"

    def cookies(self):
        """The browser cookie source, or None when the setting is off."""
        if not self.use_browser_cookies:
            return None
        from rose_bouquet.core.ytmusic import browser_cookies

        return browser_cookies()

    def shape(self) -> Shape:
        try:
            return Shape(self.visualizer_shape)
        except ValueError:
            return Shape.WAVE

    def override(self, axis: str, value: Any) -> None:
        if axis not in STYLE_AXES:
            raise KeyError(f"unknown style axis: {axis}")

        if value is None:
            self.style_overrides.pop(axis, None)
            return

        low_high = STYLE_RANGES.get(axis)
        if low_high is not None:
            low, high = low_high
            value = max(low, min(high, int(value)))
        else:
            value = bool(value)

        self.style_overrides[axis] = value

    def clear_overrides(self) -> None:
        self.style_overrides.clear()

    def value_for(self, axis: str) -> Any:
        return getattr(self.appearance().style, axis)

    # ── For-this-run-only values ──────────────────────────────────

    def use_for_run(self, name: str, value: Any) -> None:
        if not hasattr(self, name):
            raise KeyError(f"unknown preference: {name}")
        self.transient.setdefault(name, getattr(self, name))
        setattr(self, name, value)

    def set_persistent(self, name: str, value: Any) -> None:
        self.transient.pop(name, None)
        setattr(self, name, value)

    # ── Persistence ───────────────────────────────────────────────

    def to_dict(self) -> dict:
        data = {
            "theme": self.theme,
            "style": self.style,
            "style_overrides": dict(self.style_overrides),
            "section": self.section,
            "window_size": list(self.window_size),
            "sidebar_width": self.sidebar_width,
            "folders": list(self.folders),
            "download_dir": self.download_dir,
            "download_format": self.download_format,
            "scan_on_start": self.scan_on_start,
            "use_browser_cookies": self.use_browser_cookies,
            "add_downloads_to_library": self.add_downloads_to_library,
            "visualizer": self.visualizer,
            "visualizer_shape": self.visualizer_shape,
            "visualizer_alpha": self.visualizer_alpha,
            "visualizer_blur": self.visualizer_blur,
            "server": dict(self.server),
            "remote_control": self.remote_control,
            "volume": self.volume,
        }
        for name, original in self.transient.items():
            if name in data:
                data[name] = list(original) if name == "window_size" else original
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Preferences":
        theme = data.get("theme")
        style = data.get("style")

        prefs = cls(
            theme=theme if theme in THEMES else DEFAULT_THEME,
            style=style if style in STYLES else DEFAULT_STYLE,
        )

        for name in ("section", "download_dir", "download_format", "visualizer_shape"):
            value = data.get(name)
            if isinstance(value, str) and value:
                setattr(prefs, name, value)

        for name in ("scan_on_start", "add_downloads_to_library", "visualizer",
                     "visualizer_blur", "remote_control", "use_browser_cookies"):
            if name in data:
                setattr(prefs, name, bool(data[name]))

        folders = data.get("folders")
        if isinstance(folders, list):
            prefs.folders = [str(f) for f in folders if str(f).strip()]

        if isinstance(data.get("server"), dict):
            prefs.server = dict(data["server"])

        size = data.get("window_size")
        if isinstance(size, (list, tuple)) and len(size) == 2:
            try:
                prefs.window_size = (max(640, int(size[0])), max(420, int(size[1])))
            except (TypeError, ValueError):
                pass

        try:
            prefs.sidebar_width = max(150, int(data.get("sidebar_width", 210)))
        except (TypeError, ValueError):
            pass

        try:
            prefs.visualizer_alpha = max(2, min(60, int(data.get("visualizer_alpha", 15))))
        except (TypeError, ValueError):
            pass

        try:
            prefs.volume = max(0.0, min(1.0, float(data.get("volume", 0.8))))
        except (TypeError, ValueError):
            pass

        overrides = data.get("style_overrides")
        if isinstance(overrides, dict):
            for axis, value in overrides.items():
                try:
                    prefs.override(axis, value)
                except (KeyError, ValueError, TypeError):
                    logger.debug("ignoring unusable style override %r=%r", axis, value)

        return prefs

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Preferences":
        path = Path(path) if path else DEFAULT_PATH
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(first_run=True)
        except (OSError, ValueError) as exc:
            logger.warning("could not read %s, using defaults: %s", path, exc)
            return cls()

        return cls.from_dict(data) if isinstance(data, dict) else cls()

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path) if path else DEFAULT_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".part")
            temporary.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            logger.warning("could not save preferences to %s: %s", path, exc)
