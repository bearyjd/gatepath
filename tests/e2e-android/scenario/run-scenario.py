#!/usr/bin/env python3
"""Gatepath Android emulator captive-portal scenario.

Mirrors tests/e2e-docker/client/run-scenario.py in spirit: each step is a
function returning {"name", "ok", "data", "error"}; all steps aggregated
into scenario-report.json under the artifacts dir.

Step sequence:
    1.  connect           — adb connect + wait for boot_completed=1
    2.  reset_settings    — clear stale captive_portal_* globals
    3.  install           — adb install -r <apk>
    4.  reset_gateway     — POST /reset on the mockportal /log + counter
    5.  set_probe_urls    — settings put global captive_portal_*_url <url>
    6.  cycle_wifi        — svc wifi disable; sleep; svc wifi enable
    7.  wait_for_captive  — poll for the "Sign in to Wi-Fi network" notif
    8.  tap_notification  — expand panel, tap the notification
    9.  pick_chooser      — wait for chooser, tap "Gatepath" (no-op if
                            chooser absent because Android remembered)
    10. wait_portal_screen— poll logcat for GatepathCaptive activity start
    11. submit_login      — host-post mode: curl /login from host
                            ui mode: input text + tap submit (less reliable)
    12. wait_validated    — poll dumpsys connectivity for IS_VALIDATED
    13. pull_logcat       — adb logcat -d > artifacts/logcat.txt
    14. pull_audit_log    — adb pull audit_log.jsonl
    15. fetch_gateway_log — curl /log → artifacts/gateway-log.json
    16. cleanup_settings  — settings delete captive_portal_*
    17. disconnect        — adb disconnect

A failed step short-circuits the rest with rc=1. scenario-report.json is
written in a finally block regardless of failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

import adb_helper
import uiautomator_helper

# Settings.Global keys we override. Restoration deletes the same set.
CAPTIVE_KEYS = (
    "captive_portal_mode",
    "captive_portal_http_url",
    "captive_portal_https_url",
    "captive_portal_fallback_url",
)

# On-device path to Gatepath's audit log. Verified against
# android/app/src/main/java/cc/grepon/gatepath/audit/AuditLog.kt (init at
# Application.filesDir). Pulled via `adb shell run-as` because the app is
# debuggable and the file is in app-private storage.
AUDIT_LOG_RELATIVE = "files/audit_log.jsonl"
APP_PACKAGE = "cc.grepon.gatepath"


# ── Step helpers ──────────────────────────────────────────────────────────────


def step(name: str, fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """Wrap a step function so it always returns the report shape."""

    def runner(state: dict) -> dict:
        try:
            data = fn(state)
            return {"name": name, "ok": True, "data": data or {}, "error": None}
        except Exception as e:  # noqa: BLE001 — top-level guardrail
            return {
                "name": name,
                "ok": False,
                "data": {},
                "error": f"{type(e).__name__}: {e}",
            }

    return runner


def _http(url: str, method: str = "GET", data: bytes | None = None, timeout: int = 5) -> bytes:
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — test fixture
        return r.read()


# ── Steps ─────────────────────────────────────────────────────────────────────


def step_connect(state: dict) -> dict:
    serial = adb_helper.adb_connect(state["emulator_addr"], max_wait_sec=180)
    state["serial"] = serial
    return {"serial": serial}


def step_reset_settings(state: dict) -> dict:
    for k in CAPTIVE_KEYS:
        adb_helper.settings_delete(state["serial"], "global", k)
    return {"cleared": list(CAPTIVE_KEYS)}


def step_install(state: dict) -> dict:
    adb_helper.install_apk(state["serial"], state["apk_path"])
    return {"apk_path": state["apk_path"]}


def step_reset_gateway(state: dict) -> dict:
    body = _http(f"{state['mockportal_from_host_url']}/reset", method="POST")
    return {"response": body.decode("utf-8", errors="replace")[:80]}


def step_set_probe_urls(state: dict) -> dict:
    probe_url = f"{state['mockportal_host_url']}/generate_204"
    adb_helper.settings_put(state["serial"], "global", "captive_portal_mode", "1")
    adb_helper.settings_put(state["serial"], "global", "captive_portal_http_url", probe_url)
    adb_helper.settings_put(state["serial"], "global", "captive_portal_https_url", probe_url)
    adb_helper.settings_put(state["serial"], "global", "captive_portal_fallback_url", probe_url)
    # Verify
    got = {k: adb_helper.settings_get(state["serial"], "global", k) for k in CAPTIVE_KEYS}
    return {"probe_url": probe_url, "readback": got}


def step_cycle_wifi(state: dict) -> dict:
    adb_helper.cycle_wifi(state["serial"], off_pause=2.0, on_pause=8.0)
    return {"cycled": True}


def step_wait_for_captive(state: dict) -> dict:
    # Two possible signals: the notification, or the system flagging the
    # network NOT-VALIDATED. Watching dumpsys is more reliable than
    # cmd notification list (which sometimes needs special permissions).
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        dump = adb_helper.shell(state["serial"], "dumpsys connectivity", timeout=15)
        # Captive networks show NET_CAPABILITY_CAPTIVE_PORTAL in nc{...}
        # OR they're missing VALIDATED on the WIFI agent.
        if "CAPTIVE_PORTAL" in dump:
            return {"detected_via": "dumpsys", "wait_sec": int(time.monotonic() - (deadline - 45))}
        time.sleep(2)
    raise RuntimeError("captive portal not detected within 45s")


def step_tap_notification(state: dict) -> dict:
    serial = state["serial"]
    # Expand the notification shade.
    adb_helper.shell(serial, "cmd statusbar expand-notifications", timeout=10)
    time.sleep(1.5)
    # Find a notification mentioning "Sign in" (varies by locale; English emu).
    try:
        node = uiautomator_helper.wait_for_text_contains(serial, "Sign in", timeout=20)
    except RuntimeError:
        # Some images word it differently — try "Wi-Fi" + "network"
        node = uiautomator_helper.wait_for_text_contains(serial, "Wi-Fi", timeout=10)
    x, y = uiautomator_helper.midpoint(node)
    uiautomator_helper.tap(serial, x, y)
    return {"tapped_at": [x, y]}


def step_pick_chooser(state: dict) -> dict:
    serial = state["serial"]
    # If a chooser appears, pick "Gatepath". If not (Android remembered),
    # this is a no-op.
    try:
        node = uiautomator_helper.wait_for_text(serial, "Gatepath", timeout=8)
    except RuntimeError:
        return {"chooser_shown": False}
    x, y = uiautomator_helper.midpoint(node)
    uiautomator_helper.tap(serial, x, y)
    return {"chooser_shown": True, "tapped_at": [x, y]}


def step_wait_portal_screen(state: dict) -> dict:
    serial = state["serial"]
    # GatepathCaptive: "Handling captive portal for network ..." per
    # CaptivePortalActivity.kt:87. Equivalent: PortalScreen's "Network
    # Sign-In" title via UI dump. Watch logcat — cheaper, less flaky.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        log = adb_helper.shell(serial, "logcat -d -t 200 -s GatepathCaptive:I", timeout=10)
        if "Handling captive portal" in log:
            return {"detected_in_logcat": True}
        time.sleep(1.5)
    raise RuntimeError("CaptivePortalActivity did not start within 30s")


def step_submit_login(state: dict) -> dict:
    """Two modes — see scenario docstring."""
    if state["mode"] == "host-post":
        data = urllib.parse.urlencode({"user": "test"}).encode()
        try:
            _http(f"{state['mockportal_from_host_url']}/login", method="POST", data=data)
        except urllib.error.HTTPError as e:
            # /login returns 302 (Location: /generate_204); urllib raises
            # on 3xx by default unless redirects are followed. 302 is the
            # success signal here — capture and continue.
            if e.code != 302:
                raise
        return {"mode": "host-post", "outcome": "success"}
    # ui mode: drive the WebView via input. Brittle at API 34; document.
    serial = state["serial"]
    adb_helper.shell(serial, "input text user")
    adb_helper.shell(serial, "input keyevent KEYCODE_TAB")
    adb_helper.shell(serial, "input keyevent KEYCODE_ENTER")
    return {"mode": "ui", "outcome": "submitted"}


def step_wait_validated(state: dict) -> dict:
    serial = state["serial"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        dump = adb_helper.shell(serial, "dumpsys connectivity", timeout=15)
        # Find the WIFI NetworkAgentInfo line; check it has IS_VALIDATED.
        for line in dump.splitlines():
            if "ni{WIFI" in line and "IS_VALIDATED" in line and "CAPTIVE_PORTAL" not in line:
                return {"validated_in_sec": int(30 - (deadline - time.monotonic()))}
        time.sleep(2)
    raise RuntimeError("WIFI network never reached IS_VALIDATED")


def step_pull_logcat(state: dict) -> dict:
    serial = state["serial"]
    log = adb_helper.shell(serial, "logcat -d -t 2000", timeout=20)
    out = state["artifacts_dir"] / "logcat.txt"
    out.write_text(log)
    return {"path": str(out), "size": len(log)}


def step_pull_audit_log(state: dict) -> dict:
    serial = state["serial"]
    # Debug build is debuggable → run-as works.
    try:
        contents = adb_helper.shell(
            serial,
            f"run-as {APP_PACKAGE} cat {AUDIT_LOG_RELATIVE}",
            timeout=10,
            check=False,
        )
    except Exception as e:  # noqa: BLE001
        contents = ""
        err = str(e)
    else:
        err = None
    out = state["artifacts_dir"] / "audit_log.jsonl"
    out.write_text(contents)
    return {"path": str(out), "size": len(contents), "error": err}


def step_fetch_gateway_log(state: dict) -> dict:
    body = _http(f"{state['mockportal_from_host_url']}/log", timeout=5)
    out = state["artifacts_dir"] / "gateway-log.json"
    out.write_bytes(body)
    return {"path": str(out), "size": len(body)}


def step_cleanup_settings(state: dict) -> dict:
    for k in CAPTIVE_KEYS:
        adb_helper.settings_delete(state["serial"], "global", k)
    return {"deleted": list(CAPTIVE_KEYS)}


def step_disconnect(state: dict) -> dict:
    adb_helper.disconnect(state["emulator_addr"])
    return {"disconnected": True}


STEPS: list[Callable[[dict], dict]] = [
    step("connect", step_connect),
    step("reset_settings", step_reset_settings),
    step("install", step_install),
    step("reset_gateway", step_reset_gateway),
    step("set_probe_urls", step_set_probe_urls),
    step("cycle_wifi", step_cycle_wifi),
    step("wait_for_captive", step_wait_for_captive),
    step("tap_notification", step_tap_notification),
    step("pick_chooser", step_pick_chooser),
    step("wait_portal_screen", step_wait_portal_screen),
    step("submit_login", step_submit_login),
    step("wait_validated", step_wait_validated),
    step("pull_logcat", step_pull_logcat),
    step("pull_audit_log", step_pull_audit_log),
    step("fetch_gateway_log", step_fetch_gateway_log),
    step("cleanup_settings", step_cleanup_settings),
    step("disconnect", step_disconnect),
]


# ── Main ──────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--apk-path", required=True)
    p.add_argument("--emulator-addr", default="localhost:5555",
                   help="adb target: 'localhost:5555' for Docker, 'emulator-5554' for GHA")
    p.add_argument("--mockportal-host-url", default="http://10.0.2.2:18080",
                   help="URL of mockportal as seen FROM the emulator")
    p.add_argument("--mockportal-from-host-url", default="http://localhost:18080",
                   help="URL of mockportal as seen FROM the host orchestrator")
    p.add_argument("--artifacts-dir", required=True)
    p.add_argument("--mode", choices=("host-post", "ui"), default="host-post",
                   help="how to submit /login — host-post is deterministic; "
                        "ui drives the WebView via input (brittle at API 34)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    state: dict[str, Any] = {
        "emulator_addr": args.emulator_addr,
        "apk_path": args.apk_path,
        "mockportal_host_url": args.mockportal_host_url.rstrip("/"),
        "mockportal_from_host_url": args.mockportal_from_host_url.rstrip("/"),
        "artifacts_dir": Path(args.artifacts_dir),
        "mode": args.mode,
    }
    state["artifacts_dir"].mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {"rc": 0, "steps": []}
    try:
        for step_fn in STEPS:
            result = step_fn(state)
            report["steps"].append(result)
            print(
                f"  {'✓' if result['ok'] else '✗'} {result['name']}"
                f"{' — ' + result['error'] if result['error'] else ''}",
                flush=True,
            )
            if not result["ok"]:
                report["rc"] = 1
                break
    finally:
        report_path = state["artifacts_dir"] / "scenario-report.json"
        report_path.write_text(json.dumps(report, indent=2))
        print(f"\nwrote {report_path}")

    return report["rc"]


if __name__ == "__main__":
    sys.exit(main())
