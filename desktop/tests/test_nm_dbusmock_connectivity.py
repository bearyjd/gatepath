"""python-dbusmock integration test for the NM `Ip4Connectivity` wire contract.

Closes the gap named in `docs/BLOCKERS.md` under "Testing gap — NM
connectivity wire-contract is not covered in CI": today only the privileged
`tests/e2e-hwsim/` harness (root + netns + kernel module, cannot run in CI
or a sandboxed agent session) exercises a real NetworkManager service, so a
typo'd rename of `Ip4Connectivity` on the Python client side would pass
every check that *can* run automatically.

This test stands up a fake `org.freedesktop.NetworkManager` on a **private**
D-Bus system bus using `python-dbusmock` (`DBusTestCase.start_system_bus()`
spawns its own `dbus-daemon` and points `DBUS_SYSTEM_BUS_ADDRESS` at it for
this process only — it does not touch the real system bus and needs no
root/netns privilege), then asserts `gatepath.portal_monitor` reads
`Ip4Connectivity` (not the removed bare `Connectivity`) off the mocked
Device object.

Requires `python-dbusmock`, `dasbus`, and a `dbus-daemon` binary on PATH.
Skipped (not failed) when any of those are unavailable, so this file is
inert on minimal/sandboxed dev machines but active in CI and on any real
Linux desktop dev box — see `test_nm_property_contract.py` for a dependency-
free regression test covering the same property-name contract without
needing a real D-Bus daemon.
"""

from __future__ import annotations

import shutil
import unittest

import pytest

dbusmock = pytest.importorskip("dbusmock", reason="python-dbusmock not installed")
pytest.importorskip("dasbus", reason="dasbus not installed")

if shutil.which("dbus-daemon") is None:
    pytest.skip("no dbus-daemon binary on PATH", allow_module_level=True)

NM_BUS = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
DEVICE_PATH = "/org/freedesktop/NetworkManager/Devices/0"
DEVICE_IFACE = "org.freedesktop.NetworkManager.Device"
MOCK_IFACE = "org.freedesktop.DBus.Mock"

NM_DEVICE_TYPE_WIFI = 2
NM_CONNECTIVITY_PORTAL = 2
NM_CONNECTIVITY_FULL = 4


class NMConnectivityWireContractTest(dbusmock.DBusTestCase):
    """Stands up a private system bus per test class, mocking
    `org.freedesktop.NetworkManager` with only the real NM >=1.16 property
    surface (`Ip4Connectivity`, no bare `Connectivity`)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.start_system_bus()
        cls.dbus_con = cls.get_dbus(system_bus=True)

    def setUp(self) -> None:
        (self.mock_proc, self.mock_obj) = self.spawn_server(
            NM_BUS,
            NM_PATH,
            "org.freedesktop.NetworkManager",
            system_bus=True,
        )

    def tearDown(self) -> None:
        self.mock_proc.terminate()
        self.mock_proc.wait()

    def _seed_device(self, ip4_connectivity: int, device_type: int = NM_DEVICE_TYPE_WIFI) -> None:
        """Add exactly one Device object exposing the real NM 1.16+ property
        surface — `Ip4Connectivity`, never a bare `Connectivity`."""
        self.mock_obj.AddMethod(
            "org.freedesktop.NetworkManager",
            "GetDevices",
            "",
            "ao",
            f"ret = ['{DEVICE_PATH}']",
        )
        self.mock_obj.AddObject(
            DEVICE_PATH,
            DEVICE_IFACE,
            {
                "Interface": "wlan0",
                "DeviceType": device_type,
                "Ip4Connectivity": ip4_connectivity,
            },
            [],
        )

    def test_detects_captive_interface_via_ip4_connectivity(self) -> None:
        self._seed_device(ip4_connectivity=NM_CONNECTIVITY_PORTAL)

        from gatepath.portal_monitor import NMCaptiveInterfaceLookup

        result = NMCaptiveInterfaceLookup().get_captive_interface()

        assert result == "wlan0"

    def test_non_captive_device_is_not_reported(self) -> None:
        self._seed_device(ip4_connectivity=NM_CONNECTIVITY_FULL)

        from gatepath.portal_monitor import NMCaptiveInterfaceLookup

        result = NMCaptiveInterfaceLookup().get_captive_interface()

        assert result is None

    def test_bare_connectivity_property_alone_does_not_satisfy_the_contract(self) -> None:
        """Real-bus proof of the same regression `test_nm_property_contract.py`
        pins with a fake: a Device object exposing only the pre-1.16 bare
        `Connectivity` name (no `Ip4Connectivity`) must fail the read on a
        real dbusmock-backed proxy rather than silently matching, since
        reading a property NetworkManager doesn't publish raises
        `org.freedesktop.DBus.Error.InvalidArgs` — exactly what a typo'd
        rename in production code would hit against the real service."""
        self.mock_obj.AddMethod(
            "org.freedesktop.NetworkManager",
            "GetDevices",
            "",
            "ao",
            f"ret = ['{DEVICE_PATH}']",
        )
        self.mock_obj.AddObject(
            DEVICE_PATH,
            DEVICE_IFACE,
            {
                "Interface": "wlan0",
                "DeviceType": NM_DEVICE_TYPE_WIFI,
                "Connectivity": NM_CONNECTIVITY_PORTAL,  # old/wrong property name
            },
            [],
        )

        from gatepath.portal_monitor import NMCaptiveInterfaceLookup

        result = NMCaptiveInterfaceLookup().get_captive_interface()

        assert result is None


if __name__ == "__main__":
    unittest.main()
