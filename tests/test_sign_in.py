"""Signing in, and then signing in again.

The sign-in itself is Google's device-code flow, so there is not much of it to
test — the app asks for a code, shows it, and polls. What there *was* to get
wrong is the panel's memory of having done that.

Signing out left every widget exactly as the successful sign-in had left it:
the button disabled and reading "Waiting for you…", the old code still in the
label. Pressing "Sign in" a second time in the same run therefore showed a dead
button next to a code that had already been used, and the only way forward was
to restart the app. Nothing raised, nothing logged; it just could not be done
twice.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from rose_bouquet.core import innertube
from rose_bouquet.ui.theme import Appearance
from rose_bouquet.ui.youtube_native import YouTubeNativeView


def _view(tmp_path):
    QApplication.instance() or QApplication([])
    auth = innertube.Auth(tmp_path / "auth.json")
    return YouTubeNativeView(Appearance(), innertube.InnerTube(auth)), auth


def _pretend_signed_in(view, auth):
    """Walk the panel through a sign-in without touching the network."""
    view.sign_in.button.setEnabled(False)
    view.sign_in._got_code(
        innertube.DeviceCode(device_code="DEV", user_code="ABC-DEF"))
    view.sign_in.timer.stop()
    auth.tokens = innertube.Tokens(access_token="t", refresh_token="r",
                                   expires_at=2 ** 40)
    view.sign_in._polled(True)
    view._after_sign_in()


def test_you_can_sign_in_again_after_signing_out(tmp_path):
    view, auth = _view(tmp_path)
    _pretend_signed_in(view, auth)
    assert view.account.text() == "Sign out"

    view._account_pressed()                  # sign out
    assert view.account.text() == "Sign in"

    view._account_pressed()                  # and open the panel again
    assert view.sign_in.button.isEnabled()
    assert view.sign_in.button.text() == "Sign in"
    # The previous code is gone rather than sitting there looking current.
    assert view.sign_in.code_label.text() == ""
    assert not view.sign_in.code_label.isVisibleTo(view.sign_in)
    assert not view.sign_in.timer.isActive()


def test_a_failed_sign_in_can_be_retried(tmp_path):
    view, _auth = _view(tmp_path)
    view.sign_in.begin()                     # disables the button
    view.sign_in._failed("Google said no")

    assert view.sign_in.button.isEnabled()
    assert view.sign_in.button.text() == "Try again"
    assert not view.sign_in.timer.isActive()


def test_the_panel_is_where_a_signed_out_tab_opens(tmp_path):
    """There is no anonymous home feed, so the tab opens on the thing that helps."""
    view, _auth = _view(tmp_path)
    view.first_load()

    assert view.sign_in.isVisibleTo(view)
    assert not view.scroll.isVisibleTo(view)


# ── The wall the web view used to walk into ───────────────────────

def _navigation(url: str, main_frame: bool = True):
    """Ask the web view's page whether it would follow `url`.

    Returns (allowed, times it asked for the real sign-in instead).
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

    from rose_bouquet.ui.youtube_tab import _Page

    QApplication.instance() or QApplication([])
    profile = QWebEngineProfile()
    page = _Page(profile, None)
    handed_over = []
    page.sign_in_requested.connect(lambda: handed_over.append(True))

    allowed = page.acceptNavigationRequest(
        QUrl(url), QWebEnginePage.NavigationType.NavigationTypeLinkClicked,
        main_frame)

    # Qt complains loudly if the profile outlives its page.
    page.deleteLater()
    return allowed, len(handed_over)


def test_the_web_view_hands_google_login_over_instead_of_failing():
    """Google refuses embedded browsers, so walking there is a guaranteed wall.

    What this replaced: "Couldn't sign you in — this browser or app may not be
    secure", and a Try again button that could only fail the same way. No
    header or user agent changes that, because Google fingerprints the engine,
    so the request is not made at all.
    """
    allowed, handed_over = _navigation(
        "https://accounts.google.com/ServiceLogin?service=youtube")

    assert not allowed
    assert handed_over == 1


def test_it_only_stops_the_login_page_itself():
    """Everything else must still load, including the session-keeping calls.

    A signed-in session stays alive through background requests to that same
    domain. Blocking those would sign you out rather than in, so only a
    main-frame navigation counts.
    """
    allowed, handed_over = _navigation("https://www.youtube.com/watch?v=abc")
    assert allowed and handed_over == 0

    allowed, handed_over = _navigation(
        "https://accounts.google.com/RotateCookies", main_frame=False)
    assert allowed and handed_over == 0
