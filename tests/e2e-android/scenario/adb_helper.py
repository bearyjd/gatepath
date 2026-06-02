"""ADB subprocess helpers. Stdlib only — no `uiautomator2` / `pure-python-adb`.

Mirrors the spirit of tests/e2e-docker/client/run-scenario.py: clean stdlib
subprocess calls, type-annotated, focused helpers. No heavy dependencies.
"""

from __future__ import annotations

import subprocess
import time
from typing import Optional

ADB_TIMEOUT_DEFAULT = 30  # seconds


def adb(
    serial: Optional[str],
    *args: str,
    timeout: int = ADB_TIMEOUT_DEFAULT,
    check: bool = True,
) -> subprocess.CompletedProcess:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"adb {' '.join(args)} failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def adb_connect(addr: str, max_wait_sec: int = 180) -> str:
    """Connect to an emulator via TCP and wait for boot_completed=1.

    Returns the serial (typically the addr itself). Raises RuntimeError on
    timeout.

    addr is either a TCP target like "localhost:5555" (Docker path) or an
    existing emulator serial like "emulator-5554" (GHA action path).
    """
    if ":" in addr:
        # TCP target — connect first.
        adb(None, "connect", addr, timeout=15, check=False)
    deadline = time.monotonic() + max_wait_sec
    while time.monotonic() < deadline:
        try:
            got = adb(
                None,
                "-s",
                addr,
                "shell",
                "getprop",
                "sys.boot_completed",
                check=False,
                timeout=5,
            ).stdout.strip()
        except subprocess.TimeoutExpired:
            got = ""
        if got == "1":
            return addr
        time.sleep(2)
    raise RuntimeError(
        f"emulator at {addr} did not finish booting within {max_wait_sec}s"
    )


def shell(serial: str, cmd: str, timeout: int = 30, check: bool = True) -> str:
    """Run a shell command on the device; return stripped stdout."""
    r = adb(serial, "shell", cmd, timeout=timeout, check=check)
    return r.stdout.rstrip("\r\n")


def shell_full(
    serial: str, cmd: str, timeout: int = 30, check: bool = False
) -> tuple[str, str]:
    """Run a shell command; return (stdout, stderr), both stripped.

    Unlike shell(), this surfaces stderr. `pm`/`cmd` write their failure
    messages to stderr, and discarding it (as shell() does) is exactly what
    masked the stock-handler disable failure on the first PR #40 CI run, where
    pm_output came back empty with no clue why.
    """
    r = adb(serial, "shell", cmd, timeout=timeout, check=check)
    return r.stdout.rstrip("\r\n"), r.stderr.rstrip("\r\n")


def wait_for_root(serial: str, timeout: int = 30) -> bool:
    """Block until adbd is serving as root (uid 0) after `adb root`.

    `adb root` restarts adbd: the device briefly goes offline and a bare
    `adb wait-for-device` can match the *dying* daemon, so the next command
    races a half-restarted adbd and silently returns nothing (the original
    PR #40 failure — the `pm disable` ran into the void and its output was
    lost). Polling `id -u` until it reports 0 is a positive signal that the
    new root daemon is actually serving. Reconnects TCP targets, whose socket
    the restart drops.
    """
    is_tcp = ":" in serial
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_tcp:
            adb(None, "connect", serial, check=False, timeout=10)
        r = adb(serial, "shell", "id", "-u", check=False, timeout=5)
        if r.returncode == 0 and r.stdout.strip() == "0":
            return True
        time.sleep(1.0)
    return False


def settings_put(serial: str, namespace: str, key: str, value: str) -> None:
    """`settings put <namespace> <key> <value>`."""
    shell(serial, f"settings put {namespace} {key} '{value}'")


def settings_get(serial: str, namespace: str, key: str) -> str:
    """`settings get <namespace> <key>`."""
    return shell(serial, f"settings get {namespace} {key}").strip()


def settings_delete(serial: str, namespace: str, key: str) -> None:
    """`settings delete <namespace> <key>`. Never raises on absent key."""
    shell(serial, f"settings delete {namespace} {key}", check=False)


def install_apk(serial: str, apk_path: str) -> None:
    """Install (or reinstall) an APK. Generous timeout — fresh installs can
    take 30s+ on a cold emulator."""
    adb(serial, "install", "-r", apk_path, timeout=240)


def cycle_wifi(serial: str, off_pause: float = 2.0, on_pause: float = 5.0) -> None:
    """Toggle Wi-Fi off → on to force a fresh captive-portal evaluation."""
    shell(serial, "svc wifi disable")
    time.sleep(off_pause)
    shell(serial, "svc wifi enable")
    time.sleep(on_pause)


def disconnect(addr: str) -> None:
    """Tear down a TCP connection. No-op if addr isn't TCP."""
    if ":" not in addr:
        return
    adb(None, "disconnect", addr, check=False, timeout=5)
