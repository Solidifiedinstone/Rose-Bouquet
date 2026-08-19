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


def test_a_profile_folder_that_cannot_be_written_is_not_fatal(tmp_path):
    """An unwritable data folder is a problem, but not this one."""
    from rose_bouquet.ui.youtube_tab import ProfileLock

    folder = tmp_path / "readonly"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        assert ProfileLock(folder).claim()      # the profile is still ours to use
    finally:
        folder.chmod(0o700)


# ── Signing in, by the only route Google allows ───────────────────

def test_the_login_url_points_at_a_real_browser_not_ours():
    """Measured twice, and the second time properly.

    The login page loads fine inside the web view, which is what made this
    look solved. The refusal lands at the step *after* the email address —
    `/v3/signin/rejected`, "this browser or app may not be secure" — and every
    user agent gets the same answer, so it is the engine being recognised. An
    app that offers a login page here offers a wall with a form in front of it.
    """
    from rose_bouquet.ui import youtube_tab

    assert youtube_tab.SIGN_IN_URL.startswith("https://accounts.google.com/")
    # Opened in the user's own browser, so there is no `continue` back into
    # ours to be had — the session is copied afterwards, not redirected.
    assert "continue=" not in youtube_tab.SIGN_IN_URL


def test_a_jar_that_is_signed_in_is_copied_and_one_that_is_not_sends_you_to_the_browser(tmp_path, monkeypatch):
    """One press when the browser is signed in, two when it is not."""
    from rose_bouquet.ui import youtube_tab

    tab = youtube_tab.YouTubeTab.__new__(youtube_tab.YouTubeTab)
    said: list = []
    opened: list = []
    adopted: list = []
    tab._open_a_real_browser = lambda: opened.append(True)
    tab._adopt = adopted.append
    monkeypatch.setattr(youtube_tab.YouTubeTab, "status",
                        property(lambda self: type("S", (), {
                            "emit": staticmethod(lambda t, k: said.append(k))})()))

    session = [cookies.Cookie(name=n, value="x", domain=".youtube.com", secure=True)
               for n in ("SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO")]

    # Signed in over there: copied, nothing opened, done in one press.
    monkeypatch.setattr(cookies, "read", lambda *a: session)
    youtube_tab.YouTubeTab.sign_in(tab)
    assert adopted and not opened

    # Been to YouTube but not signed in: the browser opens instead.
    adopted.clear()
    monkeypatch.setattr(cookies, "read",
                        lambda *a: [cookies.Cookie(name="PREF", value="x",
                                                   domain=".youtube.com")])
    youtube_tab.YouTubeTab.sign_in(tab)
    assert opened and not adopted

    # No profile at all: still opens the browser, and says why.
    opened.clear()
    monkeypatch.setattr(cookies, "read", lambda *a: [])
    youtube_tab.YouTubeTab.sign_in(tab)
    assert opened
    assert "warning" in said


def test_youtubes_own_sign_in_button_is_what_gets_intercepted():
    """The button on the page, not just the one on our toolbar.

    YouTube's Sign in leads to Google's login, which cannot work in an
    embedded browser — it takes the email and answers `/v3/signin/rejected`,
    "this browser or app may not be secure". So the navigation is turned down
    and the sign-in that does work runs instead, which is what makes pressing
    Sign in on the page sign you in.
    """
    from PySide6.QtCore import QUrl

    from rose_bouquet.ui.youtube_tab import _is_a_login

    # Every shape YouTube's own button and Google's flow actually use.
    for link in [
        "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://www.youtube.com/",
        "https://accounts.google.com/signin/v2/identifier?service=youtube",
        "https://accounts.google.com/v3/signin/identifier?continue=https://www.youtube.com/",
        "https://accounts.google.com/AccountChooser?service=youtube",
        "https://accounts.google.com/AddSession?continue=https://www.youtube.com/",
        "https://accounts.google.com/o/oauth2/auth?client_id=x",
    ]:
        assert _is_a_login(QUrl(link)), link

    # And nothing else, because a signed-in page fetches from these hosts by
    # itself — turning those down would break the page rather than the wall.
    for ordinary in [
        "https://www.youtube.com/",
        "https://accounts.google.com/CheckCookie",
        "https://lh3.googleusercontent.com/avatar.jpg",
        "https://www.google.com/search?q=x",
        "https://music.youtube.com/",
    ]:
        assert not _is_a_login(QUrl(ordinary)), ordinary
