"""Signing in: finding browsers, and borrowing a session from one.

Google will not authenticate an embedded browser. Not with a different user
agent, not from a different address, not in a second plain window on the same
profile — the login page loads and then answers "this browser or app may not
be secure" at the password step. Every route into that wall has been tried in
this file's history; none of them work.

What does work is what the browser you already use has: the cookies. So the
app lists the browsers on this machine, and reads one of them when you pick it
and ask. Never on its own, and never any domain but YouTube's and Google's.
"""

from __future__ import annotations

import sqlite3

import pytest

from rose_bouquet.core import browsers


def _firefox_profile(tmp_path, cookies, name="Waterfox"):
    """A Firefox-family profile on disk, with a profiles.ini and a jar."""
    root = tmp_path / name
    profile = root / "abc.default-release"
    profile.mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[Profile0]\nName=default\nIsRelative=1\nPath=abc.default-release\n")

    db = sqlite3.connect(profile / "cookies.sqlite")
    db.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, "
               "path TEXT, expiry INTEGER, isSecure INTEGER, isHttpOnly INTEGER)")
    for cookie_name, value, host in cookies:
        db.execute("INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?)",
                   (cookie_name, value, host, "/", 2000000000, 1, 0))
    db.commit()
    db.close()
    return root, profile


# ── Finding the browsers ──────────────────────────────────────────

def test_a_firefox_profile_is_found_and_read(tmp_path):
    _root, profile = _firefox_profile(tmp_path, [
        ("SID", "sid", ".google.com"),
        ("LOGIN_INFO", "li", ".youtube.com"),
        ("other", "x", ".example.com"),
    ])
    found = browsers.Browser("Waterfox", "firefox", profile)

    cookies = browsers.read(found)
    assert {c.name for c in cookies} == {"SID", "LOGIN_INFO"}   # nothing else
    assert browsers.signed_in(cookies)


def test_a_browser_that_visited_youtube_but_is_not_signed_in(tmp_path):
    _root, profile = _firefox_profile(tmp_path, [("PREF", "x", ".youtube.com")])
    cookies = browsers.read(browsers.Browser("Waterfox", "firefox", profile))

    assert cookies and not browsers.signed_in(cookies)


def test_the_browser_being_open_does_not_stop_it(tmp_path):
    """Firefox holds a write lock on the live file; we read a copy.

    Reading in place is how this fails on exactly the machine that matters —
    the one with the browser open that you just signed in with.
    """
    _root, profile = _firefox_profile(tmp_path, [("SID", "sid", ".youtube.com")])

    holding = sqlite3.connect(profile / "cookies.sqlite")
    holding.execute("BEGIN EXCLUSIVE")
    try:
        cookies = browsers.read(browsers.Browser("W", "firefox", profile))
        assert browsers.signed_in(cookies)
    finally:
        holding.rollback()
        holding.close()


def test_an_unreadable_or_missing_jar_is_survivable(tmp_path):
    """"No sign-in here" is an answer; an exception is not."""
    assert browsers.read(browsers.Browser("Gone", "firefox", tmp_path / "nope")) == []

    profile = tmp_path / "broken"
    profile.mkdir()
    (profile / "cookies.sqlite").write_text("this is not a database")
    assert browsers.read(browsers.Browser("Broken", "firefox", profile)) == []


def test_discover_finds_nothing_it_cannot_read(monkeypatch, tmp_path):
    """A profile listed in the ini with no jar in it is not a browser."""
    root = tmp_path / ".waterfox"
    (root / "empty").mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[Profile0]\nName=default\nIsRelative=1\nPath=empty\n")

    monkeypatch.setattr(browsers.Path, "home", staticmethod(lambda: tmp_path))
    assert browsers.discover() == []


# ── Chromium, whose values are encrypted ──────────────────────────

def _chromium_profile(tmp_path, password, values):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.hashes import SHA1
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    profile = tmp_path / "chromium" / "Default"
    profile.mkdir(parents=True)
    key = PBKDF2HMAC(algorithm=SHA1(), length=16, salt=b"saltysalt",
                     iterations=1).derive(password)

    def encrypt(text):
        data = text.encode()
        pad = 16 - len(data) % 16
        data += bytes([pad]) * pad
        cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
        return b"v10" + cipher.update(data) + cipher.finalize()

    db = sqlite3.connect(profile / "Cookies")
    db.execute("CREATE TABLE cookies (name TEXT, value TEXT, encrypted_value BLOB,"
               " host_key TEXT, path TEXT, expires_utc INTEGER, is_secure INTEGER,"
               " is_httponly INTEGER)")
    for name, value, host in values:
        db.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?)",
                   (name, "", encrypt(value), host, "/", 13400000000000000, 1, 1))
    db.commit()
    db.close()
    return profile


def test_chromium_cookies_are_decrypted_with_whichever_key_works(tmp_path, monkeypatch):
    """The keyring password, or `peanuts` when there is no keyring.

    Both are tried, because a wrong key does not fail — AES-CBC will happily
    produce noise — so a stale or borrowed keyring entry would hand back a jar
    of mojibake that looks like a successful read.
    """
    values = [("SID", "sid-value", ".youtube.com"),
              ("__Secure-1PSID", "psid-value", ".google.com"),
              ("elsewhere", "no", ".example.com")]

    # No keyring at all: Chromium's own fallback.
    monkeypatch.setattr(browsers, "_keyring_password", lambda _e: "")
    profile = _chromium_profile(tmp_path / "a", b"peanuts", values)
    cookies = browsers.read(browsers.Browser("Chromium", "chromium", profile, "Chromium"))
    assert {c.name: c.value for c in cookies} == {
        "SID": "sid-value", "__Secure-1PSID": "psid-value"}

    # A keyring, and a jar encrypted with what is in it.
    monkeypatch.setattr(browsers, "_keyring_password", lambda _e: "from-the-keyring")
    profile = _chromium_profile(tmp_path / "b", b"from-the-keyring", values)
    cookies = browsers.read(browsers.Browser("Chromium", "chromium", profile, "Chromium"))
    assert {c.name: c.value for c in cookies} == {
        "SID": "sid-value", "__Secure-1PSID": "psid-value"}

    # A keyring entry that is stale: the fallback still gets there.
    monkeypatch.setattr(browsers, "_keyring_password", lambda _e: "wrong-password")
    profile = _chromium_profile(tmp_path / "c", b"peanuts", values)
    cookies = browsers.read(browsers.Browser("Chromium", "chromium", profile, "Chromium"))
    assert {c.name: c.value for c in cookies} == {
        "SID": "sid-value", "__Secure-1PSID": "psid-value"}


def test_a_chromium_timestamp_becomes_a_unix_one(tmp_path, monkeypatch):
    monkeypatch.setattr(browsers, "_keyring_password", lambda _e: "")
    profile = _chromium_profile(tmp_path, b"peanuts", [("SID", "v", ".youtube.com")])
    cookie = browsers.read(browsers.Browser("C", "chromium", profile, "Chromium"))[0]

    # 13400000000000000 microseconds from 1601 is 2025-07-14-ish, not 1601.
    assert 1_700_000_000 < cookie.expires < 1_900_000_000


# ── What the borrowed cookies turn into ───────────────────────────

def test_a_session_cookie_that_is_not_secure_is_not_asked_to_be_samesite_none():
    """The reason a copied sign-in once showed YouTube as signed out.

    Chromium refuses SameSite=None on a cookie that is not also Secure, and
    refuses it silently. SID, HSID and APISID are exactly that — not Secure,
    and exactly the three cookies that make up a Google session.
    """
    from PySide6.QtNetwork import QNetworkCookie

    from rose_bouquet.ui.youtube_tab import _as_qt_cookie

    insecure = browsers.Cookie(name="SID", value="x", domain=".google.com",
                               secure=False)
    secure = browsers.Cookie(name="__Secure-1PSID", value="x",
                             domain=".google.com", secure=True)

    assert _as_qt_cookie(insecure).sameSitePolicy() is QNetworkCookie.SameSite.Lax
    assert _as_qt_cookie(secure).sameSitePolicy() is QNetworkCookie.SameSite.None_


def test_the_values_a_cookie_carries_survive_the_conversion():
    from rose_bouquet.ui.youtube_tab import _as_qt_cookie

    cookie = browsers.Cookie(name="LOGIN_INFO", value="abc123",
                             domain=".youtube.com", path="/", secure=True,
                             http_only=True, expires=2000000000)
    converted = _as_qt_cookie(cookie)

    assert bytes(converted.name()).decode() == "LOGIN_INFO"
    assert bytes(converted.value()).decode() == "abc123"
    assert converted.domain() == ".youtube.com"
    assert converted.isSecure() and converted.isHttpOnly()
    assert converted.expirationDate().toSecsSinceEpoch() == 2000000000


def test_nothing_is_read_until_a_browser_is_chosen():
    """Discovery lists browsers. It does not open a single cookie jar."""
    import inspect

    source = inspect.getsource(browsers.discover)
    assert "read(" not in source
    assert "sqlite3" not in source


# ── One copy of the app owns the browser profile ──────────────────

def test_a_second_copy_of_the_app_does_not_take_the_web_profile(tmp_path):
    """The reason a sign-in kept vanishing for no visible reason.

    Two browser engines do not share a profile directory, they take turns
    overwriting it, and the file that loses is the cookie store. A second
    window — or a test harness pointed at the real data folder — silently
    undid whatever sign-in the first one had.
    """
    import os

    from rose_bouquet.ui.youtube_tab import ProfileLock

    folder = tmp_path / "youtube"
    folder.mkdir()

    first = ProfileLock(folder)
    assert first.claim()
    assert first.owner() == os.getpid()

    # Somebody else, still running. init is pid 1 and is always alive.
    (folder / "owner.pid").write_text("1")
    assert not ProfileLock(folder).claim()

    # A copy that crashed leaves a pid behind; that must not lock anyone out
    # of their own profile forever.
    (folder / "owner.pid").write_text("999999")
    assert ProfileLock(folder).claim()

    # Nor should a file that got scribbled on.
    (folder / "owner.pid").write_text("this is not a pid")
    assert ProfileLock(folder).claim()

    # And handing it back leaves nothing behind.
    third = ProfileLock(folder)
    third.claim()
    third.release()
    assert not (folder / "owner.pid").exists()
