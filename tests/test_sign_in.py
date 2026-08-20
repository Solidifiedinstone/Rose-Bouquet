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
def test_signing_in_happens_in_a_plain_window_on_the_shared_profile():
    """How NouTube does it, because this tab cannot do it itself.

    Google refuses to authenticate anything it can tell is an embedded app,
    and what gives this tab away is everything it does to the page: its own
    user agent, three injected scripts, and a request interceptor. No address
    talks it out of that — the login page loads and then answers "this browser
    or app may not be secure" at the password step.

    NouTube opens a second, ordinary window on the same session store, with
    interception turned off, and lets you sign in there. Shared store, so the
    session it gets is the session the app has.
    """
    from rose_bouquet.ui import youtube_tab

    # No login URL anywhere: the address was never the problem.
    assert not hasattr(youtube_tab, "SIGN_IN_URL")

    stripped: dict = {}

    class Scripts:
        cleared = False
        def clear(self):
            Scripts.cleared = True

    class Profile:
        @staticmethod
        def setUrlRequestInterceptor(value):
            stripped["interceptor"] = value
        @staticmethod
        def scripts():
            return Scripts()
        @staticmethod
        def setHttpUserAgent(value):
            stripped["ua"] = value

    tab = youtube_tab.YouTubeTab.__new__(youtube_tab.YouTubeTab)
    tab.profile = Profile()
    tab._plain_user_agent = "the engine's own"

    youtube_tab.YouTubeTab._undecorate(tab)

    # All three tells are removed for the duration.
    assert stripped["interceptor"] is None
    assert Scripts.cleared
    assert stripped["ua"] == "the engine's own"


def test_the_profile_is_put_back_when_the_login_window_closes():
    """Otherwise the tab keeps browsing with no ad blocking and no scripts."""
    from rose_bouquet.ui import youtube_tab

    restored: dict = {}
    tab = youtube_tab.YouTubeTab.__new__(youtube_tab.YouTubeTab)
    tab.profile = object()
    tab._login_window = object()
    tab._decorate = lambda profile: restored.__setitem__("decorated", profile)
    tab.view = type("V", (), {"reload": staticmethod(
        lambda: restored.__setitem__("reloaded", True))})()

    class P:
        @staticmethod
        def setHttpUserAgent(value):
            restored["ua"] = value
    tab.profile = P()

    youtube_tab.YouTubeTab._login_window_closed(tab)

    assert tab._login_window is None
    assert restored["decorated"] is tab.profile      # blocker and scripts back
    assert restored["ua"] == youtube_tab.USER_AGENT  # and our user agent
    assert restored["reloaded"]                      # picks up the session


def test_nothing_reads_a_browser_cookie_jar_to_sign_in():
    """The jar reader is gone, not merely unused."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("rose_bouquet.core.cookies")

    from rose_bouquet.ui import youtube_tab

    assert not hasattr(youtube_tab, "_as_qt_cookie")
    assert not hasattr(youtube_tab, "REFUSALS")
