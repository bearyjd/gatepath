"""Unit tests for the portal-launcher seam.

Every collaborator (``open_portal``, ``is_session_active``, ``detect_vpn``,
``dispatch``) is injectable, so these tests exercise the full detection→open
wiring with no GTK and no network. The dispatch stand-in records the scheduled
callbacks and lets a test drive them explicitly, mirroring the way the real
``GLib.idle_add`` would run them on the GTK main loop.
"""

from __future__ import annotations

from typing import Callable, List, Tuple

from gatepath import portal_launcher
from gatepath.portal_session import PortalPhase, PortalSession


class _RecordingDispatch:
    """A stand-in for ``GLib.idle_add`` that records each scheduled callback.

    The launcher schedules a zero-arg callback; this records it so a test can
    drive it deterministically (simulating the main loop) and assert on the
    side effects.
    """

    def __init__(self) -> None:
        self.callbacks: List[Callable[[], object]] = []

    def __call__(self, callback: Callable[[], object]) -> bool:
        self.callbacks.append(callback)
        return False

    def drive_all(self) -> None:
        for callback in list(self.callbacks):
            callback()


class _RecordingWindow:
    """Records ``open_portal`` calls."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, PortalSession]] = []

    def open_portal(self, portal_url: str, active_session: PortalSession) -> None:
        self.calls.append((portal_url, active_session))


# ---------------------------------------------------------------------------
# build_detected_session — pure, no gi, no I/O.
# ---------------------------------------------------------------------------


def test_build_detected_session_reaches_active_with_parsed_domain() -> None:
    session = portal_launcher.build_detected_session(
        "http://portal.example.net/login?x=1",
        vpn_labels=[],
    )

    assert session is not None
    assert session.phase is PortalPhase.ACTIVE
    assert session.portal_url == "http://portal.example.net/login?x=1"
    assert session.portal_domain == "portal.example.net"
    assert session.session_opened_utc is not None


def test_build_detected_session_carries_vpn_labels_and_warning_flag() -> None:
    labels = ["tun0 (unknown)", "tailscale0 (full_tunnel)"]
    session = portal_launcher.build_detected_session(
        "http://10.0.0.1/",
        vpn_labels=labels,
    )

    assert session is not None
    assert session.vpn_interfaces_detected == labels
    assert session.vpn_warning_shown is True


def test_build_detected_session_empty_vpn_labels_no_warning() -> None:
    session = portal_launcher.build_detected_session(
        "http://10.0.0.1/",
        vpn_labels=[],
    )

    assert session is not None
    assert session.vpn_interfaces_detected == []
    assert session.vpn_warning_shown is False


def test_build_detected_session_domain_falls_back_to_hostname_with_port() -> None:
    session = portal_launcher.build_detected_session(
        "http://portal.example.net:8080/login",
        vpn_labels=[],
    )

    assert session is not None
    # netloc includes the port; that is the portal_domain we carry.
    assert session.portal_domain == "portal.example.net:8080"


# ---------------------------------------------------------------------------
# PortalLauncher.on_detected — the daemon-thread callback.
# ---------------------------------------------------------------------------


def test_on_detected_happy_path_schedules_open_once() -> None:
    window = _RecordingWindow()
    dispatch = _RecordingDispatch()
    launcher = portal_launcher.PortalLauncher(
        window.open_portal,
        lambda: False,
        detect_vpn=lambda: [],
        dispatch=dispatch,
    )

    launcher.on_detected("http://portal.example.net/login")

    # One scheduled callback, nothing opened until the main loop runs it.
    assert len(dispatch.callbacks) == 1
    assert window.calls == []

    dispatch.drive_all()

    assert len(window.calls) == 1
    url, session = window.calls[0]
    assert url == "http://portal.example.net/login"
    assert session.phase is PortalPhase.ACTIVE
    assert session.portal_domain == "portal.example.net"


def test_on_detected_reentrancy_guard_blocks_second_open() -> None:
    window = _RecordingWindow()
    dispatch = _RecordingDispatch()
    launcher = portal_launcher.PortalLauncher(
        window.open_portal,
        lambda: True,  # A session is already live.
        detect_vpn=lambda: [],
        dispatch=dispatch,
    )

    launcher.on_detected("http://portal.example.net/login")

    # Neither dispatch nor open_portal fires while a session is active.
    assert dispatch.callbacks == []
    assert window.calls == []


def test_on_detected_relaunches_after_session_closes() -> None:
    window = _RecordingWindow()
    dispatch = _RecordingDispatch()
    active_flags = iter([True, False])
    launcher = portal_launcher.PortalLauncher(
        window.open_portal,
        lambda: next(active_flags),
        detect_vpn=lambda: [],
        dispatch=dispatch,
    )

    # First poll: session active -> no launch.
    launcher.on_detected("http://portal.example.net/login")
    assert dispatch.callbacks == []

    # Second poll after the session closed -> launches.
    launcher.on_detected("http://portal.example.net/login")
    dispatch.drive_all()

    assert len(window.calls) == 1
    assert window.calls[0][0] == "http://portal.example.net/login"


def test_on_detected_vpn_detect_failure_degrades_to_empty_labels() -> None:
    window = _RecordingWindow()
    dispatch = _RecordingDispatch()

    def boom() -> list[str]:
        raise OSError("if_nameindex failed")

    launcher = portal_launcher.PortalLauncher(
        window.open_portal,
        lambda: False,
        detect_vpn=boom,
        dispatch=dispatch,
    )

    launcher.on_detected("http://portal.example.net/login")
    dispatch.drive_all()

    assert len(window.calls) == 1
    _url, session = window.calls[0]
    assert session.vpn_interfaces_detected == []
    assert session.vpn_warning_shown is False


def test_on_detected_carries_vpn_labels_into_session() -> None:
    window = _RecordingWindow()
    dispatch = _RecordingDispatch()
    labels = ["wg0 (unknown)"]
    launcher = portal_launcher.PortalLauncher(
        window.open_portal,
        lambda: False,
        detect_vpn=lambda: labels,
        dispatch=dispatch,
    )

    launcher.on_detected("http://portal.example.net/login")
    dispatch.drive_all()

    _url, session = window.calls[0]
    assert session.vpn_interfaces_detected == labels
    assert session.vpn_warning_shown is True


def test_driven_callback_returns_false_so_idle_add_would_not_repeat() -> None:
    window = _RecordingWindow()
    dispatch = _RecordingDispatch()
    launcher = portal_launcher.PortalLauncher(
        window.open_portal,
        lambda: False,
        detect_vpn=lambda: [],
        dispatch=dispatch,
    )

    launcher.on_detected("http://portal.example.net/login")
    (callback,) = dispatch.callbacks
    assert callback() is False


def test_on_detected_never_raises_when_guard_predicate_raises() -> None:
    """The outer guard runs on the monitor's daemon thread, whose loop logs and
    continues — so ``on_detected`` must swallow even an exception from
    ``is_session_active`` rather than let it escape and kill the poller.
    """
    window = _RecordingWindow()
    dispatch = _RecordingDispatch()

    def _boom() -> bool:
        raise RuntimeError("guard exploded")

    launcher = portal_launcher.PortalLauncher(
        window.open_portal,
        _boom,
        detect_vpn=lambda: [],
        dispatch=dispatch,
    )

    # Must not raise; and nothing was scheduled because the guard blew up first.
    launcher.on_detected("http://portal.example.net/login")
    assert dispatch.callbacks == []
    assert window.calls == []


def test_on_detected_never_raises_when_open_portal_raises() -> None:
    """A failure inside the scheduled main-loop callback surfaces when the
    dispatch drives it — but ``on_detected`` itself (worker thread) returns
    cleanly, having only *scheduled* the call.
    """
    window = _RecordingWindow()

    def _raising_dispatch(callback: Callable[[], object]) -> bool:
        # Simulate the callback being run on the main loop and blowing up there.
        callback()
        return False

    def _open_that_raises(url: str, session: PortalSession) -> None:
        raise RuntimeError("open_portal exploded on the main loop")

    launcher = portal_launcher.PortalLauncher(
        _open_that_raises,
        lambda: False,
        detect_vpn=lambda: [],
        dispatch=_raising_dispatch,
    )

    # The worker-thread entry point must not propagate the main-loop failure.
    launcher.on_detected("http://portal.example.net/login")
    assert window.calls == []  # _open_that_raises never records a success
