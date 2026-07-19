#!/usr/bin/env python3
"""dbusmock NetworkManager harness for the gatepath E2E container.

Owns `org.freedesktop.NetworkManager` on the system bus and exposes exactly
the surface the production Gatepath stack queries:

  /org/freedesktop/NetworkManager:
      GetDevices() -> ao   (returns [Devices/0])

  /org/freedesktop/NetworkManager/Devices/0:
      Interface       (s)  = "wlan0"
      DeviceType      (u)  = 2   (NM_DEVICE_TYPE_WIFI)
      Ip4Connectivity (u)  = 2   (NM_CONNECTIVITY_PORTAL)
      Ip6Connectivity (u)  = 1   (NM_CONNECTIVITY_NONE)
      [Device.Wireless] ActiveAccessPoint (o) = AccessPoints/0

  /org/freedesktop/NetworkManager/AccessPoints/0:
      Ssid       (ay) = "CoffeeWiFi"
      Flags      (u)  = 0   (no PRIVACY bit → open)
      WpaFlags   (u)  = 0
      RsnFlags   (u)  = 0

Why these:
  * Python desktop (`gatepath.portal_monitor.NMCaptiveInterfaceLookup`) requires
    DeviceType == 2 AND Ip4Connectivity == 2, then keys off Interface to know
    which iface to hand the helper.
  * Rust helper (`gatepath-netns-helper::network_manager::NMCaptiveCheck`)
    iterates GetDevices(), matches Interface == "wlan0", reads Ip4Connectivity,
    and accepts only Ip4Connectivity == 2 as captive. It ALSO (DESK-002/003) reads
    Device.Wireless.ActiveAccessPoint → the AccessPoint's Ssid + security flags
    to capture the SSID and refuse secured networks up front; without the
    Device.Wireless + AccessPoint surface, active_ap_state() fails and
    setup_captive returns BACKEND_UNAVAILABLE. We mock an OPEN AP (all flags 0).

Process holds the bus name for its lifetime; the entrypoint reaps it when
the container shuts down.
"""

from __future__ import annotations

import subprocess
import sys
import time

import dbus
from dbus.mainloop.glib import DBusGMainLoop

NM_BUS = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
DEVICE_PATH = "/org/freedesktop/NetworkManager/Devices/0"
DEVICE_IFACE = "org.freedesktop.NetworkManager.Device"
WIRELESS_IFACE = "org.freedesktop.NetworkManager.Device.Wireless"
AP_PATH = "/org/freedesktop/NetworkManager/AccessPoints/0"
AP_IFACE = "org.freedesktop.NetworkManager.AccessPoint"
MOCK_IFACE = "org.freedesktop.DBus.Mock"

# Property values — kept as constants so a future "validated" or "limited"
# scenario test can flip them without grep-search across the file.
WIFI_DEVICE_TYPE = dbus.UInt32(2)         # NM_DEVICE_TYPE_WIFI
PORTAL_CONNECTIVITY = dbus.UInt32(2)      # NM_CONNECTIVITY_PORTAL
NO_CONNECTIVITY = dbus.UInt32(1)          # NM_CONNECTIVITY_NONE
INTERFACE_NAME = dbus.String("wlan0")


def _wait_for_bus_name(bus: dbus.SystemBus, name: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if name in bus.list_names():
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    DBusGMainLoop(set_as_default=True)

    mock_proc = subprocess.Popen(
        [
            "python3",
            "-m",
            "dbusmock",
            "--system",
            NM_BUS,
            NM_PATH,
            "org.freedesktop.NetworkManager",
        ]
    )

    bus = dbus.SystemBus()
    if not _wait_for_bus_name(bus, NM_BUS, timeout=10.0):
        sys.stderr.write("[dbusmock-nm] timed out waiting for NetworkManager bus name\n")
        mock_proc.terminate()
        return 1

    mock = dbus.Interface(bus.get_object(NM_BUS, NM_PATH), MOCK_IFACE)

    mock.AddMethod(
        "org.freedesktop.NetworkManager",
        "GetDevices",
        "",
        "ao",
        f"ret = [dbus.ObjectPath('{DEVICE_PATH}')]",
    )

    mock.AddObject(
        DEVICE_PATH,
        DEVICE_IFACE,
        {
            "Interface": INTERFACE_NAME,
            "DeviceType": WIFI_DEVICE_TYPE,
            # Ip4Connectivity, NOT bare `Connectivity`: real NM ≥1.16 has no
            # bare `Connectivity` property on the Device interface, and both
            # consumers (Python NMCaptiveInterfaceLookup, Rust NMCaptiveCheck)
            # read `Ip4Connectivity`. Publishing only the real name means a
            # consumer regressing to the legacy name fails here, loudly —
            # same philosophy as test_nm_dbusmock_connectivity.py.
            "Ip4Connectivity": PORTAL_CONNECTIVITY,
            # Real NM publishes both split properties. The Rust helper peeks
            # Ip6Connectivity on the not-captive branch (to log IPv6-only
            # portals); NONE here keeps that branch on its normal path.
            "Ip6Connectivity": NO_CONNECTIVITY,
        },
        [],
    )

    # DESK-002/003: the helper reads Device.Wireless.ActiveAccessPoint, then the
    # AccessPoint's Ssid + security flags. Add the Wireless interface to the
    # device (pointing at an AP) and an OPEN AccessPoint object.
    # NB: AddProperties is called ON the target object (signature: interface,
    # properties) — not on the NM root mock with a path argument.
    device_mock = dbus.Interface(bus.get_object(NM_BUS, DEVICE_PATH), MOCK_IFACE)
    device_mock.AddProperties(
        WIRELESS_IFACE,
        {"ActiveAccessPoint": dbus.ObjectPath(AP_PATH)},
    )
    mock.AddObject(
        AP_PATH,
        AP_IFACE,
        {
            "Ssid": dbus.ByteArray(b"CoffeeWiFi"),  # ay
            "Flags": dbus.UInt32(0),                # no PRIVACY bit → open
            "WpaFlags": dbus.UInt32(0),             # no WPA
            "RsnFlags": dbus.UInt32(0),             # no WPA2/WPA3
        },
        [],
    )

    sys.stderr.write("[dbusmock-nm] NetworkManager mock seeded; idling.\n")
    sys.stderr.flush()

    try:
        return mock_proc.wait()
    except KeyboardInterrupt:
        mock_proc.terminate()
        return mock_proc.wait()


if __name__ == "__main__":
    sys.exit(main())
