"""Signing in, and who owns the browser profile.

Reading a browser's cookie jar was how this app used to sign in, on the belief
that Google would not authenticate an embedded browser. It will: driven with
real mouse and keyboard input it answers a made-up address with "couldn't find
this account", not "this browser or app may not be secure". The refusal only
ever appeared when the login form was driven from JavaScript, which is what a
bot looks like. So there is no jar to read any more — the Sign in button goes
to Google's login and you sign in there.
"""

from __future__ import annotations

import pytest

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
def test_sign_in_goes_to_googles_login_in_the_tab():
    """Measured with real input, not with a script.

    Google answers a made-up address with "couldn't find this account" when
    the form is driven by real mouse and keyboard events, and with
    `/v3/signin/rejected`, "this browser or app may not be secure", when it is
    driven from JavaScript. The refusal is aimed at automation, not at the
    engine — so a scripted test is not evidence that a person cannot sign in
    here, and this used to be talked out of working on exactly that evidence.
    """
    from PySide6.QtCore import QUrl

    from rose_bouquet.ui import youtube_tab

    assert youtube_tab.SIGN_IN_URL.startswith("https://accounts.google.com/")
    assert "service=youtube" in youtube_tab.SIGN_IN_URL
    # No `continue`: with one Google bounces back to YouTube without ever
    # showing the form. `service=youtube` lands you back there anyway.
    assert "continue=" not in youtube_tab.SIGN_IN_URL

    # It navigates this view, and clears the jar on the way. Half a session
    # left from an abandoned sign-in sends ServiceLogin round BootstrapSession
    # until Chromium gives up with ERR_TOO_MANY_REDIRECTS, which looks like a
    # login page that will not load.
    went: list = []
    cleared: list = []
    tab = youtube_tab.YouTubeTab.__new__(youtube_tab.YouTubeTab)
    tab.view = type("V", (), {"setUrl": staticmethod(went.append)})()
    tab.profile = type("P", (), {"cookieStore": staticmethod(
        lambda: type("S", (), {"deleteAllCookies": staticmethod(
            lambda: cleared.append(True))})())})()

    youtube_tab.YouTubeTab.sign_in(tab)

    assert cleared == [True]
    assert [u.toString() for u in went] == [youtube_tab.SIGN_IN_URL]
    assert QUrl(youtube_tab.SIGN_IN_URL).host() == "accounts.google.com"


def test_nothing_reads_a_browser_cookie_jar_to_sign_in():
    """The jar reader is gone, not merely unused."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("rose_bouquet.core.cookies")

    from rose_bouquet.ui import youtube_tab

    assert not hasattr(youtube_tab, "_as_qt_cookie")
    assert not hasattr(youtube_tab, "REFUSALS")
