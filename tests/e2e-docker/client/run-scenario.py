#!/usr/bin/env python3
"""End-to-end scenario driver — runs INSIDE gatepath-client as `tester`.

Walks the desktop captive-portal flow against the captive-gateway:

  1. probe()                       → expect status="portal", portal_url set
  2. CaptiveInterfaceLookup()      → expect "wlan0" (from dbusmock NM)
  3. NetnsClient.connect()         → reach gatepath-netns-helper on system bus
  4. setup_captive("wlan0")        → helper creates /var/run/netns/gatepath,
                                     moves wlan0 in, audits the event
  5. launch_portal(portal_url)     → helper spawns the test wrapper which
                                     statically pins an IP inside the netns
                                     and exec's gatepath.portal_webview_runner
  6. wait briefly, capture xwd     → screenshot of whatever the WebView
                                     rendered (assertion is just "non-empty")
  7. kill the spawned PID          → simulates user closing the portal
  8. teardown_captive()            → helper destroys the netns; kernel pops
                                     wlan0 back to the main netns
  9. write /tmp/scenario-report.json with every observation, exit 0/1

The host-side runner reads scenario-report.json, the helper's audit log,
and the gateway's /log endpoint to make the final assertions. Doing the
asserts here AND there is intentional — this script asserts everything
visible from inside the container, the host runner asserts everything
visible from outside (gateway request log, screenshot pixel count).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Top-level imports stay light; gatepath modules are imported lazily so the
# entrypoint can show a useful error if the package install fails.
logging.basicConfig(
    level=logging.INFO,
    format="[scenario] %(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("scenario")

REPORT_PATH = Path("/tmp/scenario-report.json")
SCREENSHOT_PATH = Path("/tmp/scenario-screenshot.png")
GATEWAY_LOG_PATH = Path("/tmp/gateway-log.json")
HELPER_AUDIT_LOG = Path("/var/lib/gatepath/helper-audit.jsonl")
PROBE_URL = os.environ.get("GATEPATH_PROBE_URL", "http://connectivity-check.ubuntu.com/")
WEBVIEW_DWELL_SECONDS = float(os.environ.get("GATEPATH_WEBVIEW_DWELL_SECONDS", "6"))


@dataclasses.dataclass
class Step:
    """A single E2E step's outcome — JSON-serialisable observation."""

    name: str
    ok: bool
    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    error: str | None = None


def run_step(name: str, fn) -> Step:
    log.info("→ %s", name)
    try:
        data = fn() or {}
        step = Step(name=name, ok=True, data=data)
        log.info("✓ %s  %s", name, json.dumps(step.data, default=str))
        return step
    except Exception as exc:  # noqa: BLE001
        log.exception("✗ %s", name)
        return Step(name=name, ok=False, error=f"{type(exc).__name__}: {exc}")


def step_reset_gateway() -> dict[str, Any]:
    # mockportal's redirect-then-204 counter is process-global. Reset it
    # so probe #1 is the FIRST request that counts.
    #
    # podman-compose's `depends_on` doesn't have a healthcheck condition
    # in our setup, so the client container can race ahead of the
    # gateway. Retry with backoff until nginx is accepting connections
    # OR we run out of attempts.
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    attempts = 20
    delay = 0.25
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request("http://172.30.0.2/reset", method="POST")
            with urllib.request.urlopen(req, timeout=2) as resp:
                body = resp.read().decode("utf-8")
            return {"reset_response": body, "attempts": attempt}
        except (urllib.error.URLError, ConnectionRefusedError, OSError) as exc:
            last_error = exc
            time.sleep(delay)
    raise AssertionError(
        f"gateway never came up: {attempts} attempts × {delay}s = "
        f"{attempts * delay:.1f}s elapsed. Last error: {last_error}"
    )


def step_probe() -> dict[str, Any]:
    from gatepath.portal_probe import probe  # noqa: PLC0415

    result = probe(url=PROBE_URL, timeout=5)
    if result.status != "portal":
        raise AssertionError(f"expected status=portal, got {result.status!r}: {result.message}")
    if not result.portal_url:
        raise AssertionError("portal_url was empty on a 'portal' result")
    return {"status": result.status, "portal_url": result.portal_url}


def step_nm_lookup() -> dict[str, Any]:
    # NMCaptiveInterfaceLookup is the dasbus-backed production impl —
    # `CaptiveInterfaceLookup` in the same module is the Protocol it
    # satisfies. The method returns None (not raise) when no device
    # qualifies; we surface that as a step failure.
    from gatepath.portal_monitor import NMCaptiveInterfaceLookup  # noqa: PLC0415

    iface = NMCaptiveInterfaceLookup().get_captive_interface()
    if iface != "wlan0":
        raise AssertionError(f"expected wlan0, got {iface!r}")
    return {"interface": iface}


def step_helper_connect() -> tuple[Any, dict[str, Any]]:
    from gatepath.netns_client import NetnsClient  # noqa: PLC0415

    client = NetnsClient.connect()
    return client, {"connected": True}


def step_setup(client: Any) -> dict[str, Any]:
    from gatepath.netns_client import SetupSuccess  # noqa: PLC0415

    result = client.setup_captive("wlan0")
    if not isinstance(result, SetupSuccess):
        raise AssertionError(f"setup refused: {result}")
    return {"netns_path": result.netns_path}


def step_launch(client: Any, portal_url: str) -> dict[str, Any]:
    from gatepath.netns_client import LaunchPortalSuccess  # noqa: PLC0415

    result = client.launch_portal(portal_url)
    if not isinstance(result, LaunchPortalSuccess):
        raise AssertionError(f"launch refused: {result}")
    return {"pid": result.pid}


def step_dwell_and_screenshot(pid: int) -> dict[str, Any]:
    # Give the WebView a moment to render the portal page.
    time.sleep(WEBVIEW_DWELL_SECONDS)

    # scrot grabs DISPLAY's root window and writes PNG. Xvfb at :99 has
    # exactly one screen so root == WebView's containing window.
    rc = subprocess.run(
        ["scrot", "--overwrite", str(SCREENSHOT_PATH)],
        check=False,
        env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")},
    ).returncode
    screenshot_size = SCREENSHOT_PATH.stat().st_size if SCREENSHOT_PATH.exists() else 0

    # Whether the subprocess is still alive — if it crashed in <dwell secs
    # we want to know.
    proc_dir = Path(f"/proc/{pid}")
    alive = proc_dir.exists()

    return {
        "xwd_rc": rc,
        "screenshot_size": screenshot_size,
        "subprocess_alive": alive,
    }


def step_kill(pid: int) -> dict[str, Any]:
    # SIGTERM the WebView subprocess to simulate "user closed the window".
    # The helper's PortalSubprocessExited signal would normally drive
    # teardown via desktop_isolation.py; we don't subscribe in the scenario
    # since the helper's audit log records the exit independently.
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"already_exited": True}

    # Wait for it to actually exit (up to 5s).
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return {"exited_after_sigterm": True}
        time.sleep(0.1)
    # Escalate.
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return {"escalated_to_sigkill": True}


def step_teardown(client: Any) -> dict[str, Any]:
    from gatepath.netns_client import TeardownSuccess  # noqa: PLC0415

    result = client.teardown_captive()
    if not isinstance(result, TeardownSuccess):
        raise AssertionError(f"teardown refused: {result}")
    return {"torn_down": True}


def step_snapshot_gateway_log() -> dict[str, Any]:
    # Snapshot the mockportal request log over the captive subnet. The
    # main netns has full network while wlan0 is here; setup_captive
    # moves it away and teardown returns it without L3 config. So the
    # caller must invoke this either before setup or with a manual
    # re-config after teardown. The scenario order keeps it pre-setup.
    import urllib.request  # noqa: PLC0415

    with urllib.request.urlopen("http://172.30.0.2/log", timeout=3) as resp:
        body = resp.read()
    GATEWAY_LOG_PATH.write_bytes(body)
    return {"bytes": len(body), "path": str(GATEWAY_LOG_PATH)}


def step_check_audit() -> dict[str, Any]:
    # The helper writes /var/lib/gatepath/helper-audit.jsonl as root with
    # 0640 perms. Tester (uid 1000) can't read the contents — that's the
    # production deployment shape and we want to honour it. We only check
    # that the file *exists* and has *non-zero size*, which os.stat does
    # without requiring read perms. assertions.py (running host-side after
    # the root-owned entrypoint cp's the file out) does the contents-level
    # validation.
    if not HELPER_AUDIT_LOG.exists():
        raise AssertionError(f"audit log not found at {HELPER_AUDIT_LOG}")
    size = HELPER_AUDIT_LOG.stat().st_size
    if size == 0:
        raise AssertionError("audit log exists but is empty")
    return {"audit_size_bytes": size}


def main() -> int:
    steps: list[Step] = []
    portal_url = ""
    client = None
    pid = 0

    steps.append(run_step("reset_gateway", step_reset_gateway))
    # Don't bail if reset fails — degraded but probe might still work.

    steps.append(run_step("probe", step_probe))
    if not steps[-1].ok:
        return write_report(steps, rc=1)
    portal_url = steps[-1].data["portal_url"]

    # Capture the gateway request log while the main netns still has
    # network. setup_captive moves wlan0 into the gatepath netns and
    # teardown returns it naked — the main netns has no working iface
    # in between. With the WebView in production this misses its
    # traffic; here the WebView exits fast (no GTK runtime), so the
    # captured log is complete.
    steps.append(run_step("snapshot_gateway_log", step_snapshot_gateway_log))

    steps.append(run_step("nm_lookup", step_nm_lookup))
    if not steps[-1].ok:
        return write_report(steps, rc=1)

    def _connect():
        nonlocal client
        client, data = step_helper_connect()
        return data
    steps.append(run_step("helper_connect", _connect))
    if not steps[-1].ok:
        return write_report(steps, rc=1)

    steps.append(run_step("setup", lambda: step_setup(client)))
    if not steps[-1].ok:
        return write_report(steps, rc=1)

    steps.append(run_step("launch", lambda: step_launch(client, portal_url)))
    if not steps[-1].ok:
        return write_report(steps, rc=1)
    pid = steps[-1].data["pid"]

    steps.append(run_step("dwell_and_screenshot", lambda: step_dwell_and_screenshot(pid)))
    # Don't bail on screenshot failure — push through to teardown.

    steps.append(run_step("kill", lambda: step_kill(pid)))

    steps.append(run_step("teardown", lambda: step_teardown(client)))

    steps.append(run_step("audit_check", step_check_audit))

    rc = 0 if all(s.ok for s in steps) else 1
    return write_report(steps, rc=rc)


def write_report(steps: list[Step], rc: int) -> int:
    payload = {
        "rc": rc,
        "steps": [dataclasses.asdict(s) for s in steps],
    }
    REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str))
    log.info("report → %s (rc=%d)", REPORT_PATH, rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
