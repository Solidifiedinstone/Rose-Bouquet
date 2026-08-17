"""Borrowing a sign-in from the browser you already use.

Google will not let you sign in from inside an embedded browser. It is not the
user agent — it fingerprints the engine, so no header changes it — and the
answer is always "Couldn't sign you in / this browser or app may not be
secure". There is no version of a login form in this app that works.

What does work is not signing in here at all. You are already signed in to
YouTube in your own browser; a sign-in *is* a handful of cookies; so the app
copies those across and the web view is signed in, in YouTube's own interface,
without a password ever being typed into anything of ours.

This is the same jar `ytmusic.browser_cookies()` points yt-dlp at, read here
directly because Qt's cookie store wants the values rather than a file path.

Nothing leaves the machine and nothing is written back — the file is opened
read-only, through a copy, so a running browser cannot be disturbed by it and a
locked database cannot stop it.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: The domains worth copying. Deliberately narrow: this reads a cookie jar,
#: which is the most sensitive file in a home directory, and there is no reason
#: for a music player to hold anything but the site it is showing you.
DOMAINS = (".youtube.com", "youtube.com", ".google.com", "accounts.google.com",
           ".googlevideo.com")

#: The cookies that actually constitute being signed in. Used to tell "you are
#: signed in elsewhere" from "you have merely visited YouTube", so the app can
#: say which rather than copying nothing and looking broken.
AUTH_COOKIES = frozenset({
    "SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO",
    "__Secure-1PSID", "__Secure-3PSID",
})


@dataclass
class Cookie:
    """One cookie, in the terms Qt's store wants it in."""

    name: str
    value: str
    domain: str
    path: str = "/"
    #: Unix time, or 0 for a session cookie.
    expires: int = 0
    secure: bool = True
    http_only: bool = False

    @property
    def url(self) -> str:
        host = self.domain[1:] if self.domain.startswith(".") else self.domain
        return f"https://{host}{self.path}"


def firefox_profiles() -> list[Path]:
    """Every Firefox or Waterfox profile on this machine that has a cookie jar.

    Waterfox first: it is the same format, and someone who has both has
    usually made the fork the one they actually use.
    """
    found: list[Path] = []
    for root in (Path.home() / ".waterfox",
                 Path.home() / ".mozilla" / "firefox"):
        index = root / "profiles.ini"
        if not index.exists():
            continue

        parser = ConfigParser()
        try:
            parser.read(index)
        except Exception:                    # noqa: BLE001 — a broken ini is not fatal
            continue

        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            path = parser[section].get("Path", "")
            if not path:
                continue
            folder = ((root / path)
                      if parser[section].get("IsRelative", "1") == "1"
                      else Path(path))
            if (folder / "cookies.sqlite").exists():
                found.append(folder)

    return found


def read(profile: Optional[Path] = None) -> list[Cookie]:
    """The YouTube and Google cookies out of a browser profile.

    The jar is copied before it is read. A browser that is open holds a write
    lock on it, and reading it in place is how you get "database is locked" on
    the one machine where it matters — the one the user is sitting at with the
    browser they just signed in with.
    """
    profiles = [profile] if profile else firefox_profiles()
    for folder in profiles:
        jar = Path(folder) / "cookies.sqlite"
        if not jar.exists():
            continue

        with tempfile.TemporaryDirectory() as scratch:
            copy = Path(scratch) / "cookies.sqlite"
            try:
                shutil.copy2(jar, copy)
                # The write-ahead log holds anything the browser has not
                # checkpointed yet, which on a fresh sign-in is the sign-in.
                for suffix in ("-wal", "-shm"):
                    extra = jar.with_name(jar.name + suffix)
                    if extra.exists():
                        shutil.copy2(extra, copy.with_name(copy.name + suffix))
            except OSError as exc:
                logger.warning("could not read %s: %s", jar, exc)
                continue

            try:
                found = _query(copy)
            except sqlite3.Error as exc:
                logger.warning("could not read cookies from %s: %s", jar, exc)
                continue

        if found:
            return found

    return []


def _query(jar: Path) -> list[Cookie]:
    connection = sqlite3.connect(f"file:{jar}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in DOMAINS)
        rows = connection.execute(
            f"SELECT name, value, host, path, expiry, isSecure, isHttpOnly "  # noqa: S608
            f"FROM moz_cookies WHERE host IN ({placeholders})",
            DOMAINS,
        ).fetchall()
    finally:
        connection.close()

    return [
        Cookie(name=name, value=value, domain=host, path=path or "/",
               expires=int(expiry or 0), secure=bool(secure),
               http_only=bool(http_only))
        for name, value, host, path, expiry, secure, http_only in rows
    ]


def signed_in(cookies: list[Cookie]) -> bool:
    """Whether this set of cookies is an account rather than a visit."""
    return bool({cookie.name for cookie in cookies} & AUTH_COOKIES)
