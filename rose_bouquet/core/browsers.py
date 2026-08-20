"""Finding the browsers on this machine, and borrowing a YouTube sign-in.

Google will not authenticate an embedded browser. It is not the user agent —
it fingerprints what the app does to the page — and there is no address, no
device code and no second window that gets past it: the login page loads and
then answers "this browser or app may not be secure" at the password step.

A sign-in *is* a handful of cookies, though, and the browser you already use
has them. So this finds the browsers that are installed, and — only when you
pick one and ask — reads the YouTube cookies out of it.

Two families, because there are only two cookie stores in the world:

* **Firefox and its forks** keep `cookies.sqlite`, in plain text, listed in a
  `profiles.ini` beside it.
* **Chrome and its forks** keep `Cookies`, with the values encrypted. On Linux
  the key is derived from a password held in the desktop keyring, falling back
  to the well-known `peanuts` that Chromium uses when there is no keyring.

Nothing is written back, ever. Every database is copied before it is opened,
so a running browser cannot be disturbed by this and a locked file cannot stop
it. Only cookies for YouTube and Google are read: this is the most sensitive
file in a home directory, and a music player has no business with the rest of
it.
"""

from __future__ import annotations

import base64
import configparser
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

#: The domains worth reading. Deliberately narrow.
DOMAINS = (".youtube.com", "youtube.com", ".google.com", "google.com",
           "accounts.google.com", ".googlevideo.com")

#: The cookies that actually constitute being signed in, used to tell "signed
#: in over there" from "has merely visited YouTube".
AUTH_COOKIES = frozenset({
    "SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO",
    "__Secure-1PSID", "__Secure-3PSID",
})


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class Browser:
    """A browser profile found on this machine."""

    name: str                    # what to call it on screen
    family: str                  # "firefox" | "chromium"
    profile: Path                # the folder holding the cookie store
    #: Which keyring entry holds this browser's key, for the Chromium family.
    keyring: str = ""

    @property
    def key(self) -> str:
        return f"{self.family}:{self.profile}"

    @property
    def cookie_store(self) -> Path:
        return self.profile / ("cookies.sqlite" if self.family == "firefox"
                               else "Cookies")


# ── Finding them ──────────────────────────────────────────────────

#: Firefox-family roots: (display name, path relative to home, flatpak id).
_FIREFOX_FAMILY = (
    ("Firefox", ".mozilla/firefox", "org.mozilla.firefox/.mozilla/firefox"),
    ("Waterfox", ".waterfox", "net.waterfox.waterfox/.waterfox"),
    ("LibreWolf", ".librewolf", "io.gitlab.librewolf-community/.librewolf"),
    ("Zen Browser", ".zen", "app.zen_browser.zen/.zen"),
    ("Floorp", ".floorp", "one.ablaze.floorp/.floorp"),
    ("Tor Browser", ".tb/tor-browser/Browser/TorBrowser/Data/Browser", ""),
)

#: Chromium-family roots: (display name, path under ~/.config, flatpak id,
#: keyring entry).
_CHROMIUM_FAMILY = (
    ("Chrome", "google-chrome", "com.google.Chrome/config/google-chrome", "Chrome"),
    ("Chrome Beta", "google-chrome-beta", "", "Chrome"),
    ("Chromium", "chromium", "org.chromium.Chromium/config/chromium", "Chromium"),
    ("Brave", "BraveSoftware/Brave-Browser",
     "com.brave.Browser/config/BraveSoftware/Brave-Browser", "Brave"),
    ("Microsoft Edge", "microsoft-edge",
     "com.microsoft.Edge/config/microsoft-edge", "Microsoft Edge"),
    ("Vivaldi", "vivaldi", "com.vivaldi.Vivaldi/config/vivaldi", "Vivaldi"),
    ("Opera", "opera", "com.opera.Opera/config/opera", "Opera"),
    ("Thorium", "thorium", "", "Thorium"),
)


def discover() -> list[Browser]:
    """Every browser profile on this machine with a cookie store in it.

    Native installs and Flatpaks both, because on a machine with Flatpaks the
    one you actually use is as likely to be the sandboxed one — and its
    cookies live somewhere completely different.
    """
    found: list[Browser] = []
    home = Path.home()

    for name, relative, flatpak in _FIREFOX_FAMILY:
        roots = [home / relative]
        if flatpak:
            roots.append(home / ".var/app" / flatpak)
        for root in roots:
            for profile in _firefox_profiles(root):
                label = name if root == home / relative else f"{name} (Flatpak)"
                found.append(Browser(label, "firefox", profile))

    for name, relative, flatpak, keyring in _CHROMIUM_FAMILY:
        roots = [home / ".config" / relative]
        if flatpak:
            roots.append(home / ".var/app" / flatpak)
        for root in roots:
            for profile in _chromium_profiles(root):
                label = name if root == home / ".config" / relative else f"{name} (Flatpak)"
                found.append(Browser(label, "chromium", profile, keyring))

    return found


def _firefox_profiles(root: Path) -> list[Path]:
    """Profiles listed in `profiles.ini` that actually have a cookie jar.

    Read from the index rather than by globbing, so a profile someone deleted
    the contents of does not show up as a browser you can sign in from.
    """
    index = root / "profiles.ini"
    if not index.exists():
        return []

    parser = configparser.ConfigParser()
    try:
        parser.read(index)
    except Exception:                     # noqa: BLE001 — a broken ini is not fatal
        return []

    profiles = []
    for section in parser.sections():
        if not section.lower().startswith("profile"):
            continue
        relative = parser[section].get("Path", "")
        if not relative:
            continue
        folder = ((root / relative) if parser[section].get("IsRelative", "1") == "1"
                  else Path(relative))
        if (folder / "cookies.sqlite").exists():
            profiles.append(folder)
    return profiles


def _chromium_profiles(root: Path) -> list[Path]:
    """`Default` and any `Profile N` under a Chromium-family config folder."""
    if not root.is_dir():
        return []
    profiles = []
    for name in ["Default"] + sorted(p.name for p in root.glob("Profile *")):
        folder = root / name
        if (folder / "Cookies").exists():
            profiles.append(folder)
    return profiles


# ── Reading them ──────────────────────────────────────────────────

def readable(browser: Browser) -> str:
    """Why this browser cannot be read, or an empty string if it can.

    Chromium keeps its cookie values encrypted and `cryptography` is what
    decrypts them. It is a declared dependency, but a source checkout or a
    trimmed package can be missing it, and "nothing could be read" is a poor
    way to say "one package is not installed".
    """
    if not browser.cookie_store.exists():
        return "that profile has no cookie store"
    if browser.family == "chromium":
        try:
            import cryptography  # noqa: F401
        except ImportError:
            return ("reading Chrome-family browsers needs the cryptography "
                    "package")
    return ""


def read(browser: Browser) -> list[Cookie]:
    """The YouTube and Google cookies in this browser, or an empty list.

    Never raises: an unreadable jar, a browser that changed its schema, a
    missing decryption key — all of them mean "no sign-in here", which the
    caller has to handle anyway.
    """
    why = readable(browser)
    if why:
        logger.warning("cannot read %s: %s", browser.name, why)
        return []

    try:
        if browser.family == "firefox":
            return _read_firefox(browser)
        return _read_chromium(browser)
    except Exception as exc:              # noqa: BLE001 — reading someone else's file
        logger.warning("could not read cookies from %s: %s", browser.name, exc)
        return []


def signed_in(cookies: Iterable[Cookie]) -> bool:
    """Whether these cookies amount to being signed in to Google."""
    return bool({c.name for c in cookies} & AUTH_COOKIES)


def _copied(path: Path):
    """A copy of a database, so a running browser is never disturbed.

    Firefox holds a write lock on the live file, which is how reading in place
    fails on exactly the machine that matters: the one with the browser open
    that you just signed in with.
    """
    descriptor, name = tempfile.mkstemp(suffix=".sqlite")
    os.close(descriptor)
    shutil.copy(path, name)
    # The write-ahead log holds anything the browser has not checkpointed yet,
    # which on a browser that is open is where the cookie you just got lives.
    for extra in ("-wal", "-shm"):
        beside = Path(str(path) + extra)
        if beside.exists():
            shutil.copy(beside, name + extra)
    return Path(name)


def _matching(domain: str) -> bool:
    return any(domain == d or domain.endswith(d) for d in DOMAINS)


def _read_firefox(browser: Browser) -> list[Cookie]:
    copy = _copied(browser.cookie_store)
    try:
        db = sqlite3.connect(f"file:{copy}?immutable=1", uri=True)
        rows = db.execute(
            "SELECT name, value, host, path, expiry, isSecure, isHttpOnly "
            "FROM moz_cookies"
        ).fetchall()
        db.close()
    finally:
        _cleanup(copy)

    return [
        Cookie(name=name, value=value, domain=host, path=path or "/",
               expires=int(expiry or 0), secure=bool(secure),
               http_only=bool(http_only))
        for name, value, host, path, expiry, secure, http_only in rows
        if _matching(host or "")
    ]


def _read_chromium(browser: Browser) -> list[Cookie]:
    keys = _chromium_keys(browser)
    copy = _copied(browser.cookie_store)
    try:
        db = sqlite3.connect(f"file:{copy}?immutable=1", uri=True)
        rows = db.execute(
            "SELECT name, value, encrypted_value, host_key, path, "
            "expires_utc, is_secure, is_httponly FROM cookies"
        ).fetchall()
        db.close()
    finally:
        _cleanup(copy)

    cookies = []
    for name, value, encrypted, host, path, expires, secure, http_only in rows:
        if not _matching(host or ""):
            continue
        plain = value or _decrypt(encrypted, keys)
        if not plain:
            continue
        cookies.append(Cookie(
            name=name, value=plain, domain=host, path=path or "/",
            # Chromium counts microseconds from 1601; Unix counts seconds
            # from 1970. 11644473600 is the gap.
            expires=int(expires / 1_000_000 - 11_644_473_600) if expires else 0,
            secure=bool(secure), http_only=bool(http_only),
        ))
    return cookies


def _cleanup(copy: Path) -> None:
    for extra in ("", "-wal", "-shm"):
        Path(str(copy) + extra).unlink(missing_ok=True)


def _chromium_keys(browser: Browser) -> list[bytes]:
    """Every AES key this browser's cookies might be encrypted with.

    On Linux the password lives in the desktop keyring under "<Browser> Safe
    Storage". Without a keyring — a bare session, a container — Chromium
    falls back to the literal `peanuts`.

    Both are returned rather than one, because a wrong key does not fail: it
    decrypts to rubbish, and a stale or borrowed keyring entry would hand
    back a jar of mojibake that looks like a successful read. Whichever key
    produces text is the right one, and that is decided per cookie.
    """
    from cryptography.hazmat.primitives.hashes import SHA1
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    passwords = [p for p in (_keyring_password(browser.keyring), "peanuts") if p]
    keys = []
    for password in dict.fromkeys(passwords):
        kdf = PBKDF2HMAC(algorithm=SHA1(), length=16, salt=b"saltysalt",
                         iterations=1)
        keys.append(kdf.derive(password.encode()))
    return keys


def _keyring_password(entry: str) -> str:
    """Ask the desktop keyring for a browser's Safe Storage password."""
    if not entry:
        return ""
    for attribute in ("application", "xdg:schema"):
        value = entry.lower() if attribute == "application" else "chrome_libsecret_os_crypt_password_v2"
        try:
            found = subprocess.run(
                ["secret-tool", "lookup", attribute, value],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return ""


def _decrypt(blob: Optional[bytes], keys: list[bytes]) -> str:
    """One encrypted cookie value, as text, using whichever key works."""
    if not blob:
        return ""

    prefix, body = blob[:3], blob[3:]
    if prefix not in (b"v10", b"v11"):
        # Not encrypted after all — older profiles store some values plain.
        try:
            return blob.decode()
        except UnicodeDecodeError:
            return ""

    for key in keys:
        text = _with_key(body, key)
        if text:
            return text
    return ""


def _with_key(body: bytes, key: bytes) -> str:
    """Decrypt with one key, and say whether what came out is a cookie.

    A wrong key still "works" — AES-CBC will happily produce sixteen bytes of
    noise per block — so the check is whether the result is the sort of text a
    cookie value is: printable ASCII. That is what tells a stale keyring entry
    from the right one.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    try:
        plain = decryptor.update(body) + decryptor.finalize()
    except ValueError:
        return ""

    if plain and plain[-1] <= 16:
        plain = plain[:-plain[-1]]        # PKCS#7
    # Chrome 127 and later prepend a SHA-256 of the domain to the value.
    if len(plain) > 32 and not _readable(plain[:32]):
        plain = plain[32:]

    return plain.decode() if _readable(plain) else ""


def _readable(data: bytes) -> bool:
    """Whether these bytes are the sort of text a cookie value is."""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return False
    return bool(text) and all(32 <= ord(c) < 127 for c in text)


def local_state_key(browser: Browser) -> Optional[bytes]:
    """The key recorded in `Local State`, for profiles that keep one there.

    Unused on Linux, where the key is derived from the keyring password, but
    read here so the shape of the file is documented in one place rather than
    rediscovered on the machine where it matters.
    """
    state = browser.profile.parent / "Local State"
    if not state.exists():
        return None
    try:
        data = json.loads(state.read_text())
        encoded = data["os_crypt"]["encrypted_key"]
    except (OSError, ValueError, KeyError):
        return None
    return base64.b64decode(encoded)
