"""Borrowing a sign-in, and not borrowing anything else.

Google refuses to authenticate an embedded browser — it fingerprints the
engine, so no user agent changes it — which means a login form in this app is
not something that can be made to work. The way in is to copy the session you
already have in your own browser.

That makes this module the one place in a music player that opens a cookie
jar, so the tests are as much about restraint as about function: it reads only
YouTube and Google, only through a copy, and only for the values Qt needs.
"""

from __future__ import annotations

import sqlite3

from rose_bouquet.core import cookies


def _jar(path, rows):
    """A Firefox cookie database holding `rows`."""
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, name TEXT, "
        "value TEXT, host TEXT, path TEXT, expiry INTEGER, isSecure INTEGER, "
        "isHttpOnly INTEGER)")
    connection.executemany(
        "INSERT INTO moz_cookies (name, value, host, path, expiry, isSecure, "
        "isHttpOnly) VALUES (?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


SIGNED_IN = [
    ("SID", "abc", ".google.com", "/", 2000000000, 1, 1),
    ("LOGIN_INFO", "xyz", ".youtube.com", "/", 2000000000, 1, 1),
    ("PREF", "f1=1", ".youtube.com", "/", 2000000000, 0, 0),
]


def test_it_reads_a_sign_in_out_of_a_profile(tmp_path):
    _jar(tmp_path / "cookies.sqlite", SIGNED_IN)

    found = cookies.read(tmp_path)

    assert len(found) == 3
    assert cookies.signed_in(found)
    login = next(c for c in found if c.name == "LOGIN_INFO")
    assert login.value == "xyz"
    assert login.http_only and login.secure
    assert login.url == "https://youtube.com/"


def test_visiting_youtube_is_not_being_signed_in(tmp_path):
    """A jar full of preference cookies must not be reported as an account.

    Otherwise pressing Sign in copies 30 cookies, says it worked, and leaves
    you looking at a signed-out page wondering what it did.
    """
    _jar(tmp_path / "cookies.sqlite",
         [("PREF", "f1=1", ".youtube.com", "/", 2000000000, 0, 0),
          ("VISITOR_INFO1_LIVE", "q", ".youtube.com", "/", 2000000000, 1, 1)])

    found = cookies.read(tmp_path)
    assert found and not cookies.signed_in(found)


def test_it_takes_nothing_but_youtube_and_google(tmp_path):
    """It is a cookie jar. Everything not needed is left where it is."""
    _jar(tmp_path / "cookies.sqlite", SIGNED_IN + [
        ("session", "secret", ".mybank.example", "/", 2000000000, 1, 1),
        ("token", "secret", "mail.protonmail.com", "/", 2000000000, 1, 1),
    ])

    hosts = {c.domain for c in cookies.read(tmp_path)}

    assert hosts <= set(cookies.DOMAINS)
    assert not any("bank" in h or "proton" in h for h in hosts)


def test_a_missing_or_unreadable_jar_is_survivable(tmp_path):
    assert cookies.read(tmp_path) == []

    (tmp_path / "cookies.sqlite").write_text("this is not a database")
    assert cookies.read(tmp_path) == []


def test_the_browser_being_open_does_not_stop_it(tmp_path):
    """Firefox holds a write lock on the live file; we read a copy.

    Reading in place is how this fails on exactly the machine that matters —
    the one with the browser open that you just signed in with.
    """
    path = tmp_path / "cookies.sqlite"
    _jar(path, SIGNED_IN)

    holding = sqlite3.connect(path)
    holding.execute("BEGIN EXCLUSIVE")
    try:
        assert cookies.signed_in(cookies.read(tmp_path))
    finally:
        holding.rollback()
        holding.close()


# ── Getting them past Chromium ────────────────────────────────────

def test_a_session_cookie_that_is_not_secure_is_not_asked_to_be_samesite_none():
    """The reason a copied sign-in still showed YouTube as signed out.

    Chromium refuses SameSite=None on a cookie that is not also Secure, and
    refuses it silently. SID, HSID and APISID are exactly that — not Secure,
    and exactly the three cookies that make up a Google session — so asking
    for None on everything dropped the sign-in on the way in and left five of
    the eight auth cookies to look like a working copy.
    """
    from PySide6.QtNetwork import QNetworkCookie

    from rose_bouquet.ui.youtube_tab import _as_qt_cookie

    insecure = cookies.Cookie(name="SID", value="x", domain=".google.com",
                              secure=False)
    secure = cookies.Cookie(name="__Secure-1PSID", value="x",
                            domain=".google.com", secure=True)

    assert _as_qt_cookie(insecure).sameSitePolicy() is QNetworkCookie.SameSite.Lax
    assert _as_qt_cookie(secure).sameSitePolicy() is QNetworkCookie.SameSite.None_


def test_the_values_a_cookie_carries_survive_the_conversion():
    from rose_bouquet.ui.youtube_tab import _as_qt_cookie

    cookie = cookies.Cookie(name="LOGIN_INFO", value="abc123",
                            domain=".youtube.com", path="/", secure=True,
                            http_only=True, expires=2000000000)
    converted = _as_qt_cookie(cookie)

    assert bytes(converted.name()).decode() == "LOGIN_INFO"
    assert bytes(converted.value()).decode() == "abc123"
    assert converted.domain() == ".youtube.com"
    assert converted.isSecure() and converted.isHttpOnly()
    assert converted.expirationDate().toSecsSinceEpoch() == 2000000000
