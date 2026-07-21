"""Tests for ``GatepathWindow.is_session_active`` — the portal launcher's
re-entrancy predicate.

``is_session_active`` reads ``self._controller.session.phase``, so exercising
the *real* window needs a live GTK 4 host; that path is covered by an
``importorskip`` guard. Where GTK 4 is absent the module falls back to the stub
``GatepathWindow``, whose ``is_session_active`` must raise ``ImportError`` like
the other stub methods — that contract is checked headlessly.
"""

from __future__ import annotations

import pytest

from gatepath.portal_session import (
    PortalPhase,
    PortalSession,
    to_active,
    to_detected,
)


def _make_active_session() -> PortalSession:
    """Build an ACTIVE session via the proper transition path."""
    s = PortalSession()
    s = s.transition_or_none(PortalPhase.MONITORING)
    assert s is not None
    s = to_detected(
        s,
        ssid="Cafe-WiFi",
        gateway_ip="192.168.1.1",
        portal_url="http://portal.cafe.example/login",
        portal_domain="portal.cafe.example",
        vpn_interfaces_detected=[],
        vpn_warning_shown=False,
    )
    assert s is not None
    s = to_active(s)
    assert s is not None
    return s


def _gtk_or_skip():
    """Return the live ``GatepathWindow`` + a presented ``Adw.Application``,
    or skip where GTK 4 / libadwaita is unavailable.
    """
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # type: ignore[import-untyped]  # noqa: PLC0415
    except (ValueError, ImportError):
        pytest.skip("GTK 4 / libadwaita namespace unavailable")

    from gatepath.window import GatepathWindow  # noqa: PLC0415

    Adw.init()
    app = Adw.Application(application_id="com.ventouxlabs.GatepathTest")
    return GatepathWindow, app


# ── live-window path (GTK-capable hosts only) ──────────────────────────


def test_is_session_active_false_on_fresh_window() -> None:
    GatepathWindow, app = _gtk_or_skip()
    window = GatepathWindow(application=app)
    # No session registered on the controller yet.
    assert window.is_session_active() is False


def test_is_session_active_true_after_set_active() -> None:
    GatepathWindow, app = _gtk_or_skip()
    window = GatepathWindow(application=app)

    window._controller.set_active(_make_active_session())
    assert window.is_session_active() is True


def test_is_session_active_false_after_session_closes() -> None:
    """After the live session closes, the predicate re-arms (returns False) so
    monitoring can open the next portal.
    """
    from gatepath.portal_session import CloseReason  # noqa: PLC0415

    GatepathWindow, app = _gtk_or_skip()
    window = GatepathWindow(application=app)

    window._controller.set_active(_make_active_session())
    assert window.is_session_active() is True

    window._controller.close(CloseReason.USER_DISMISSED)
    assert window.is_session_active() is False


# ── stub path (headless / no PyGObject) ────────────────────────────────


def test_stub_is_session_active_raises_importerror() -> None:
    """Where GTK is absent, the stub ``GatepathWindow.is_session_active`` must
    raise ``ImportError`` rather than silently returning — matching the other
    stub methods. Skipped on GTK-capable hosts where the real class is loaded.
    """
    try:
        import gi  # noqa: PLC0415

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
    except (ImportError, ValueError):
        pass  # stub path is active — proceed to assert on it.
    else:
        pytest.skip("GTK 4 available — real class loaded, not the stub")

    from gatepath.window import GatepathWindow  # noqa: PLC0415

    stub = GatepathWindow.__new__(GatepathWindow)
    with pytest.raises(ImportError):
        stub.is_session_active()
