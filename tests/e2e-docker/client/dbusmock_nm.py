#!/usr/bin/env python3
"""dbusmock NetworkManager harness for the gatepath E2E container.

Owns `org.freedesktop.NetworkManager` on the system bus and exposes exactly
the surface the production Gatepath stack queries:

  /org/freedesktop/NetworkManager:
      GetDevices() -> ao   (returns [Devices/0])

  /org/freedesktop/NetworkManager/Devices/0:
      Interface     (s)  = "wlan0"
      DeviceType    (u)  = 2   (NM_DEVICE_TYPE_WIFI)
      Connectivity  (u)  = 2   (NM_CONNECTIVITY_PORTAL)

Why these three:
  * Python desktop (`gatepath.portal_monitor.CaptiveInterfaceLookup`) requires
    DeviceType == 2 AND Connectivity == 2, then keys off Interface to know
    which iface to hand the helper.
  * Rust helper (`gatepath-netns-helper::network_manager::NMCaptiveCheck`)
    iterates GetDevices(), matches Interface == "wlan0", reads Connectivity,
    and accepts only Connectivity == 2 as captive.

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
MOCK_IFACE = "org.freedesktop.DBus.Mock"

# Property values — kept as constants so a future "validated" or "limited"
# scenario test can flip them without grep-search across the file.
WIFI_DEVICE_TYPE = dbus.UInt32(2)         # NM_DEVICE_TYPE_WIFI
PORTAL_CONNECTIVITY = dbus.UInt32(2)      # NM_CONNECTIVITY_PORTAL
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
            "Connectivity": PORTAL_CONNECTIVITY,
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
