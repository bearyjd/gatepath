"""Tests for the GTK-independent isolation glue in window.py and the
wait-result → CloseReason mapping in desktop_isolation.

The full window lifecycle (engage → hide → wait → unhide) requires GTK +
WebKit + a real DesktopIsolation; that path is exercised by PR-C's
mock-captive harness. Here we pin the pure-logic gating + mapping.
"""

from __future__ import annotations

from typing import Optional

from gatepath.desktop_isolation import (
    SubprocessExit,
    WaitInterrupted,
    WaitTimeout,
    wait_result_to_close_reason,
)
from gatepath.portal_monitor import CaptiveInterfaceLookup
from gatepath.portal_session import CloseReason


class _StubLookup(CaptiveInterfaceLookup):
    def __init__(self, interface: Optional[str]) -> None:
        self._interface = interface

    def get_captive_interface(self) -> Optional[str]:
        return self._interface


# ── isolation_should_engage gating ─────────────────────────────────────


def test_should_engage_false_when_isolation_missing() -> None:
    from gatepath.desktop_isolation import isolation_should_engage  # noqa: PLC0415

    assert isolation_should_engage(None, _StubLookup("wlan0")) is False


def test_should_engage_false_when_lookup_missing() -> None:
    from gatepath.desktop_isolation import isolation_should_engage  # noqa: PLC0415

    # Use a sentinel object as a non-None isolation handle. The function
    # only checks for None, never invokes the isolation methods.
    assert isolation_should_engage(object(), None) is False  # type: ignore[arg-type]


def test_should_engage_false_when_no_captive_interface() -> None:
    from gatepath.desktop_isolation import isolation_should_engage  # noqa: PLC0415

    assert isolation_should_engage(object(), _StubLookup(None)) is False  # type: ignore[arg-type]


def test_should_engage_true_when_all_present() -> None:
    from gatepath.desktop_isolation import isolation_should_engage  # noqa: PLC0415

    assert isolation_should_engage(object(), _StubLookup("wlan0")) is True  # type: ignore[arg-type]


# ── wait_result_to_close_reason ────────────────────────────────────────


def test_clean_exit_maps_to_portal_completed() -> None:
    result = SubprocessExit(pid=4242, exit_code=0, signal_num=0)
    assert wait_result_to_close_reason(result) == CloseReason.PORTAL_COMPLETED


def test_nonzero_exit_maps_to_error() -> None:
    result = SubprocessExit(pid=4242, exit_code=1, signal_num=0)
    assert wait_result_to_close_reason(result) == CloseReason.ERROR


def test_signaled_exit_maps_to_error() -> None:
    result = SubprocessExit(pid=4242, exit_code=-1, signal_num=9)
    assert wait_result_to_close_reason(result) == CloseReason.ERROR


def test_timeout_maps_to_timeout_close_reason() -> None:
    assert wait_result_to_close_reason(WaitTimeout()) == CloseReason.TIMEOUT


def test_interrupted_maps_to_user_dismissed() -> None:
    assert wait_result_to_close_reason(WaitInterrupted()) == CloseReason.USER_DISMISSED


def test_unknown_result_raises_typeerror() -> None:
    import pytest  # noqa: PLC0415

    with pytest.raises(TypeError):
        wait_result_to_close_reason("not a wait result")  # type: ignore[arg-type]
