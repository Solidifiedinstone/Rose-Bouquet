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
def test_sign_in_opens_a_real_browser_rather_than_this_one():
    """An embedded browser is the one place Google is liable to refuse."""
    from rose_bouquet.ui import youtube_tab

    assert youtube_tab.SIGN_IN_URL.startswith("https://accounts.google.com/")
    assert "service=youtube" in youtube_tab.SIGN_IN_URL

    opened, said = [], []
    tab = youtube_tab.YouTubeTab.__new__(youtube_tab.YouTubeTab)
    tab.status = type("S", (), {"emit": staticmethod(lambda t, k: said.append(k))})()

    import webbrowser
    real = webbrowser.open
    webbrowser.open = lambda url: opened.append(url)
    try:
        youtube_tab.YouTubeTab.sign_in(tab)
    finally:
        webbrowser.open = real

    assert opened == [youtube_tab.SIGN_IN_URL]
    assert said == ["info"]


def test_nothing_reads_a_browser_cookie_jar_to_sign_in():
    """The jar reader is gone, not merely unused."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("rose_bouquet.core.cookies")

    from rose_bouquet.ui import youtube_tab

    assert not hasattr(youtube_tab, "_as_qt_cookie")
    assert not hasattr(youtube_tab, "REFUSALS")
