"""The window must actually render a portal when isolation is unavailable.

Regression test for the case where `open_portal` armed the 10-minute timer and
left the monitoring page up: no WebView was ever constructed, so a user whose
deployment can't reach the netns helper saw nothing happen and got a `timeout`
audit entry ten minutes later. That is every Flatpak install — its sandbox has
no `--system-talk-name` for the helper — which is the primary distribution.

`SECURITY_MODEL.md` specifies the unconfined in-process path for exactly those
deployments, so rendering here implements the documented design rather than
weakening it.

Needs a real GTK 4 / libadwaita / WebKit runtime; skipped where absent. The
`gtk-widgets` CI job exists so "skipped" isn't the only outcome anywhere.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: F401
except ValueError:  # pragma: no cover - depends on host runtime
    pytest.skip("GTK 4 / libadwaita runtime not available", allow_module_level=True)

try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit  # noqa: F401
except ValueError:  # pragma: no cover - depends on host runtime
    pytest.skip("WebKitGTK 6.0 not available", allow_module_level=True)

from gatepath.portal_session import (  # noqa: E402
    CloseReason,
    PortalPhase,
    PortalSession,
    to_active,
    to_detected,
)
from gatepath.window import GatepathWindow  # noqa: E402

PORTAL_URL = "http://portal.invalid/login"


def _active_session() -> PortalSession:
    s = PortalSession().transition_or_none(PortalPhase.MONITORING)
    assert s is not None
    s = to_detected(
        s,
        ssid="Cafe-WiFi",
        gateway_ip="192.168.1.1",
        portal_url=PORTAL_URL,
        portal_domain="portal.invalid",
        vpn_interfaces_detected=[],
        vpn_warning_shown=False,
    )
    assert s is not None
    s = to_active(s)
    assert s is not None
    return s


def _widget_names(root) -> list[str]:
    names = [type(root).__name__]
    child = root.get_first_child() if hasattr(root, "get_first_child") else None
    while child is not None:
        names.extend(_widget_names(child))
        child = child.get_next_sibling()
    return names


@pytest.fixture
def window():
    Adw.init()
    app = Adw.Application(application_id="com.ventouxlabs.GatepathTest")
    win = GatepathWindow(application=app, isolation=None, captive_interface_lookup=None)
    yield win
    win.destroy()


def test_monitoring_view_has_no_webview_before_a_portal_opens(window) -> None:
    names = _widget_names(window.get_content())
    assert not [n for n in names if "WebView" in n]


def test_open_portal_renders_a_webview_without_isolation(window) -> None:
    """The regression itself.

    Before the fix this asserted nothing existed to assert on: open_portal
    called set_active() and returned, leaving the monitoring page up.
    """
    window.open_portal(PORTAL_URL, _active_session())

    names = _widget_names(window.get_content())
    assert [n for n in names if "WebView" in n], (
        "no WebView in the window after open_portal — the user is looking at the "
        "monitoring page with a 10-minute timer running and no sign-in page"
    )


def test_session_goes_active_and_arms_the_timer(window) -> None:
    window.open_portal(PORTAL_URL, _active_session())
    assert window._controller.session is not None
    assert window._controller.session.phase == PortalPhase.ACTIVE
    assert window._controller.is_timer_armed


def test_dismiss_tears_the_portal_down_and_restores_monitoring(window) -> None:
    window.open_portal(PORTAL_URL, _active_session())
    assert [n for n in _widget_names(window.get_content()) if "WebView" in n]

    window.dismiss_session()

    names = _widget_names(window.get_content())
    assert not [n for n in names if "WebView" in n], "the WebView outlived its session"
    assert window._controller.session is not None
    assert window._controller.session.phase == PortalPhase.COMPLETED


def test_webkit_failure_reports_instead_of_hanging(window, monkeypatch) -> None:
    """A WebView we can't build must not leave the timer running silently.

    That failure mode — session Active, nothing on screen, `timeout` written
    ten minutes later — is the bug this file exists to prevent.
    """
    import gatepath.portal_webview as pw

    def _boom(**kwargs):
        raise RuntimeError("no WebKit here")

    monkeypatch.setattr(pw, "make_webview", _boom)

    window.open_portal(PORTAL_URL, _active_session())

    assert not [n for n in _widget_names(window.get_content()) if "WebView" in n]
    assert window._controller.session is not None
    assert window._controller.session.phase == PortalPhase.ERROR, (
        "a portal that cannot render must close the session, not leave it Active"
    )
    assert not window._controller.is_timer_armed
