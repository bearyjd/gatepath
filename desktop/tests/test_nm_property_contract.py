"""Dependency-free regression test for the NM `Ip4Connectivity` wire contract.

`network_manager.rs` (the Rust helper) and `portal_monitor.py` (this Python
client) both talk to the same NetworkManager Device object and must agree on
the property name: `Ip4Connectivity`, not the bare `Connectivity` that was
removed in NetworkManager 1.16 (see the module docs on both sides). Only the
privileged `tests/e2e-hwsim/` harness exercises a real NetworkManager, so
nothing else catches a typo'd rename on the Python side — the existing
`test_captive_interface_lookup.py` only pins a hand-rolled fake that never
touches the real property name.

This test doesn't need a real (or mocked) D-Bus daemon: it injects a fake
`dasbus.connection` module via `sys.modules` so `_check_device_for_captive`'s
lazy `from dasbus.connection import SystemMessageBus` import resolves to a
plain Python double. The double raises `AttributeError` for any property it
wasn't explicitly given, mirroring how a real dasbus proxy behaves when a
D-Bus property doesn't exist on the remote object — so a regression to
`device.Connectivity` fails this test the same way it would fail against
real NetworkManager >=1.16.

For a fuller integration proof against an actual (mocked) NetworkManager
service on a private bus, see `test_nm_dbusmock_connectivity.py` — that one
needs `python-dbusmock` + a `dbus-daemon` binary and is skipped when either
is unavailable; this file has no such dependency and always runs.
"""

from __future__ import annotations

import sys
import types
from typing import Optional
from unittest.mock import MagicMock

import pytest

from gatepath.portal_monitor import NM_CONNECTIVITY_PORTAL, NM_DEVICE_TYPE_WIFI


class _FakeNMDeviceProxy:
    """Stand-in for a dasbus Device proxy exposing exactly the properties
    given — anything else raises AttributeError, like a real proxy would for
    a property NetworkManager doesn't actually publish."""

    def __init__(self, **properties: object) -> None:
        self._properties = properties

    def __getattr__(self, name: str) -> object:
        try:
            return self._properties[name]
        except KeyError:
            raise AttributeError(
                f"NM device proxy has no property {name!r}"
            ) from None


def _install_fake_dasbus(device_path_to_proxy: dict[str, _FakeNMDeviceProxy]) -> None:
    """Inject a fake `dasbus.connection` module so
    `from dasbus.connection import SystemMessageBus` resolves without the
    real dasbus package (not installed in every test environment)."""

    fake_connection_module = types.ModuleType("dasbus.connection")

    class _FakeSystemMessageBus:
        def get_proxy(
            self,
            service_name: str,
            object_path: str,
            interface_name: str,
        ) -> _FakeNMDeviceProxy:
            del service_name, interface_name
            return device_path_to_proxy[object_path]

    fake_connection_module.SystemMessageBus = _FakeSystemMessageBus  # type: ignore[attr-defined]

    fake_dasbus_pkg = types.ModuleType("dasbus")
    fake_dasbus_pkg.connection = fake_connection_module  # type: ignore[attr-defined]

    sys.modules["dasbus"] = fake_dasbus_pkg
    sys.modules["dasbus.connection"] = fake_connection_module


@pytest.fixture(autouse=True)
def _clean_dasbus_module_injection():
    """Ensure each test starts without a leftover fake dasbus module and
    removes its injection afterward, so this file doesn't leak state into
    other test modules that import the real dasbus (if installed)."""
    for name in ("dasbus", "dasbus.connection"):
        sys.modules.pop(name, None)
    yield
    for name in ("dasbus", "dasbus.connection"):
        sys.modules.pop(name, None)


def _check_device_for_captive(device_path: str) -> Optional[str]:
    # Re-import fresh each call so the lazy `from dasbus.connection import
    # SystemMessageBus` inside the function body re-resolves against
    # whatever fake module the test just installed.
    from gatepath.portal_monitor import _check_device_for_captive as impl

    return impl(device_path)


def test_reads_ip4_connectivity_and_detects_captive_portal() -> None:
    """The real wire contract: a WiFi device with Ip4Connectivity == PORTAL
    is detected as captive and its interface name is returned."""
    _install_fake_dasbus(
        {
            "/org/freedesktop/NetworkManager/Devices/0": _FakeNMDeviceProxy(
                DeviceType=NM_DEVICE_TYPE_WIFI,
                Ip4Connectivity=NM_CONNECTIVITY_PORTAL,
                Interface="wlan0",
            ),
        }
    )

    result = _check_device_for_captive("/org/freedesktop/NetworkManager/Devices/0")

    assert result == "wlan0"


def test_ignores_bare_connectivity_property_pin() -> None:
    """Regression guard: a device that only exposes the removed bare
    `Connectivity` property (not `Ip4Connectivity`) must NOT be detected as
    captive. This is the exact typo'd-rename scenario docs/BLOCKERS.md warns
    about — reading the wrong property name on real NetworkManager >=1.16
    raises `InvalidArgs`, which the production code's broad except turns
    into a silent `None`. If a future change accidentally reverts to
    `device.Connectivity`, this test fails loudly because the fake proxy
    below has no `Ip4Connectivity` property to satisfy it, so the
    `AttributeError` propagates up through the same except path and yields
    None here too — but the companion positive-path test above would also
    then wrongly return None for a genuinely captive device, catching the
    regression."""
    _install_fake_dasbus(
        {
            "/org/freedesktop/NetworkManager/Devices/0": _FakeNMDeviceProxy(
                DeviceType=NM_DEVICE_TYPE_WIFI,
                Connectivity=NM_CONNECTIVITY_PORTAL,  # old/wrong property name
                Interface="wlan0",
            ),
        }
    )

    result = _check_device_for_captive("/org/freedesktop/NetworkManager/Devices/0")

    # Only Ip4Connectivity is missing here (Connectivity doesn't count), so
    # the lookup must fail closed rather than accidentally reading the wrong
    # property and reporting captive.
    assert result is None


def test_non_wifi_device_is_never_captive_regardless_of_connectivity() -> None:
    _install_fake_dasbus(
        {
            "/org/freedesktop/NetworkManager/Devices/0": _FakeNMDeviceProxy(
                DeviceType=1,  # NM_DEVICE_TYPE_ETHERNET
                Ip4Connectivity=NM_CONNECTIVITY_PORTAL,
                Interface="eth0",
            ),
        }
    )

    result = _check_device_for_captive("/org/freedesktop/NetworkManager/Devices/0")

    assert result is None


def test_full_lookup_picks_captive_device_using_ip4_connectivity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercises NMCaptiveInterfaceLookup.get_captive_interface end to end
    (GetDevices() -> per-device property reads), not just the per-device
    helper, so the whole client-side path is pinned to Ip4Connectivity."""
    from gatepath.portal_monitor import NMCaptiveInterfaceLookup

    device_paths = [
        "/org/freedesktop/NetworkManager/Devices/0",
        "/org/freedesktop/NetworkManager/Devices/1",
    ]
    devices = {
        device_paths[0]: _FakeNMDeviceProxy(
            DeviceType=1,  # ethernet, not captive-eligible
            Ip4Connectivity=NM_CONNECTIVITY_PORTAL,
            Interface="eth0",
        ),
        device_paths[1]: _FakeNMDeviceProxy(
            DeviceType=NM_DEVICE_TYPE_WIFI,
            Ip4Connectivity=NM_CONNECTIVITY_PORTAL,
            Interface="wlan0",
        ),
    }
    _install_fake_dasbus(devices)

    fake_connection_module = sys.modules["dasbus.connection"]
    root_proxy = MagicMock()
    root_proxy.GetDevices.return_value = device_paths
    original_get_proxy = fake_connection_module.SystemMessageBus.get_proxy

    def _get_proxy(self, service_name, object_path, interface_name):  # type: ignore[no-untyped-def]
        if object_path == "/org/freedesktop/NetworkManager":
            return root_proxy
        return original_get_proxy(self, service_name, object_path, interface_name)

    fake_connection_module.SystemMessageBus.get_proxy = _get_proxy  # type: ignore[method-assign]

    lookup = NMCaptiveInterfaceLookup()
    result = lookup.get_captive_interface()

    assert result == "wlan0"
