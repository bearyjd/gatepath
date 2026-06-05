#!/usr/bin/env python3
"""Drive the gatepath netns helper over a SINGLE persistent D-Bus connection.

`busctl`/`dbus-send` open a fresh, short-lived connection per call. That breaks
the helper in two ways:
  * the name-watch (auto-teardown when the owning UI dies, DESK-5b.6) fires the
    instant SetupCaptive returns, because the one-shot caller "disconnected" —
    so the session is gone before LaunchPortal runs;
  * LaunchPortal must come from the SetupCaptive *owner* (SenderMismatch),
    which a new connection isn't.

Holding ONE connection for the whole SetupCaptive → LaunchPortal → wait →
TeardownCaptive sequence is exactly how the real GUI behaves, and fixes both.

argv: <interface> <portal_url> <verdict_path> [verdict_timeout_s]
Emits a JSON result object on stdout; human notes on stderr. Exit 0 iff the
full session (setup + launch + teardown) succeeded.
"""

from __future__ import annotations

import json
import os
import sys
import time

import dbus

BUS_NAME = "cc.grepon.Gatepath.NetNsHelper"
OBJ_PATH = "/cc/grepon/Gatepath/NetNsHelper"
IFACE = "cc.grepon.Gatepath.NetNsHelper1"

# Generous per-call D-Bus timeouts: SetupCaptive does the PHY move + in-netns
# wpa_supplicant association + DHCP, which is the slow part.
CALL_TIMEOUT = 90.0


def log(msg: str) -> None:
    print(f"      [drive] {msg}", file=sys.stderr, flush=True)


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: drive.py <interface> <portal_url> <verdict_path> [verdict_timeout_s]",
              file=sys.stderr)
        return 2
    interface = sys.argv[1]
    portal_url = sys.argv[2]
    verdict_path = sys.argv[3]
    verdict_timeout = float(sys.argv[4]) if len(sys.argv) > 4 else 25.0

    result: dict = {
        "setup_netns": None,
        "launch_pid": None,
        "teardown": None,
        "error": None,
    }

    # ONE connection for the whole session — do NOT let it drop until teardown.
    bus = dbus.SystemBus()
    helper = dbus.Interface(bus.get_object(BUS_NAME, OBJ_PATH), IFACE)

    # --- SetupCaptive ---
    try:
        netns = str(helper.SetupCaptive(interface, timeout=CALL_TIMEOUT))
        result["setup_netns"] = netns
        log(f"SetupCaptive → {netns}")
    except dbus.DBusException as exc:
        result["error"] = f"SetupCaptive: {exc.get_dbus_name()}: {exc}"
        log(result["error"])
        print(json.dumps(result))
        return 1

    # Clear any stale verdict so the wait below sees a fresh one.
    try:
        os.unlink(verdict_path)
    except FileNotFoundError:
        pass

    # --- LaunchPortal (same connection = same owner) ---
    try:
        pid = int(helper.LaunchPortal(portal_url, "", "", "", timeout=CALL_TIMEOUT))
        result["launch_pid"] = pid
        log(f"LaunchPortal → pid {pid}")
    except dbus.DBusException as exc:
        result["error"] = f"LaunchPortal: {exc.get_dbus_name()}: {exc}"
        log(result["error"])
        _try_teardown(helper, result)
        print(json.dumps(result))
        return 1

    # --- Wait for the runner's no-leak verdict (written from inside the netns) ---
    deadline = time.monotonic() + verdict_timeout
    while time.monotonic() < deadline:
        try:
            if os.path.getsize(verdict_path) > 0:
                log(f"runner verdict present at {verdict_path}")
                break
        except OSError:
            pass
        time.sleep(0.5)
    else:
        log(f"runner verdict NOT seen within {verdict_timeout:.0f}s")

    # --- TeardownCaptive ---
    ok = _try_teardown(helper, result)
    print(json.dumps(result))
    return 0 if ok else 1


def _try_teardown(helper, result: dict) -> bool:
    try:
        helper.TeardownCaptive(timeout=CALL_TIMEOUT)
        result["teardown"] = "ok"
        log("TeardownCaptive → ok")
        return True
    except dbus.DBusException as exc:
        msg = f"TeardownCaptive: {exc.get_dbus_name()}: {exc}"
        result["teardown"] = msg
        if not result["error"]:
            result["error"] = msg
        log(msg)
        return False


if __name__ == "__main__":
    sys.exit(main())
