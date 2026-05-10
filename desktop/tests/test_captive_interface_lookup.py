"""Tests for the captive-interface-lookup surface in portal_monitor.

The real :py:class:`NMCaptiveInterfaceLookup` talks to NetworkManager via
dasbus; we exercise it indirectly through the Protocol contract using a
hand-rolled fake. Real-NM coverage lives in PR-C's mock-captive harness.
"""

from __future__ import annotations

from typing import Optional

from gatepath.portal_monitor import (
    NM_CONNECTIVITY_PORTAL,
    NM_DEVICE_TYPE_WIFI,
    CaptiveInterfaceLookup,
)


class FakeCaptiveLookup(CaptiveInterfaceLookup):
    """Hand-rolled lookup for tests that pin the Protocol contract."""

    def __init__(self, interface: Optional[str]) -> None:
        self._interface = interface
        self.calls = 0

    def get_captive_interface(self) -> Optional[str]:
        self.calls += 1
        return self._interface


def test_protocol_runtime_check_accepts_compatible_object() -> None:
    lookup = FakeCaptiveLookup("wlan0")
    # runtime_checkable Protocol means isinstance works on duck-typed
    # objects. Pin the contract.
    assert isinstance(lookup, CaptiveInterfaceLookup)


def test_lookup_returns_interface_name_when_captive() -> None:
    lookup = FakeCaptiveLookup("wlan0")
    assert lookup.get_captive_interface() == "wlan0"


def test_lookup_returns_none_when_no_captive_device() -> None:
    lookup = FakeCaptiveLookup(None)
    assert lookup.get_captive_interface() is None


def test_constants_match_helper_expectations() -> None:
    # Helper's NM_CONNECTIVITY_PORTAL is hardcoded to 2; pin parity here
    # so a future refactor that drifts these values fails this test
    # rather than silently breaking the captive flow.
    assert NM_CONNECTIVITY_PORTAL == 2
    assert NM_DEVICE_TYPE_WIFI == 2


def test_lookup_called_once_per_get() -> None:
    """Pin: each call to get_captive_interface should invoke the
    underlying query exactly once. Caching the result for the lifetime of
    the lookup would race against the user roaming networks."""
    lookup = FakeCaptiveLookup("wlan0")
    lookup.get_captive_interface()
    lookup.get_captive_interface()
    assert lookup.calls == 2
