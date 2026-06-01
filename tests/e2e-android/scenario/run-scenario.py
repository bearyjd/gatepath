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
import re
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

# AOSP stock captive-portal handler. With both Gatepath and the stock app
# registered, the system either silently dispatches to the stock app or
# shows a chooser — both modes blocked the test on the first PR #40 CI run
# (artifact: pick_chooser recorded chooser_shown=False, then
# wait_portal_screen timed out because the intent went to the stock app).
# Disabling the stock package leaves Gatepath as the only handler so the
# system auto-dispatches the CAPTIVE_PORTAL intent directly to
# CaptivePortalActivity with no UI gate. Requires `adb root` (userdebug
# emulator images support it; production images do not — step records the
# outcome and continues either way).
STOCK_HANDLER_PKG = "com.android.captiveportallogin"

# The implicit-intent action the system fires when the captive "Sign in"
# notification is tapped. Gatepath's CaptivePortalActivity registers for it
# (see AndroidManifest.xml). Used to enumerate competing handlers.
CAPTIVE_PORTAL_ACTION = "android.net.conn.CAPTIVE_PORTAL"


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


def _captive_portal_handlers(serial: str) -> list[str]:
    """Package names of every activity registered for the CAPTIVE_PORTAL
    sign-in intent, via `cmd package query-activities --brief` (one clean
    `pkg/component` per line). Returns [] if nothing parseable comes back, in
    which case the caller falls back to the known stock package."""
    out, _ = adb_helper.shell_full(
        serial,
        f"cmd package query-activities --brief -a {CAPTIVE_PORTAL_ACTION} "
        "-c android.intent.category.DEFAULT",
        timeout=15,
        check=False,
    )
    pkgs: list[str] = []
    for line in out.splitlines():
        m = re.fullmatch(r"([\w.]+)/[\w.$]+", line.strip())
        if m and m.group(1) not in pkgs:
            pkgs.append(m.group(1))
    return pkgs


def step_disable_stock_handler(state: dict) -> dict:
    """Make Gatepath the sole CAPTIVE_PORTAL handler so the system
    auto-dispatches the sign-in intent straight to CaptivePortalActivity.

    With both Gatepath and the AOSP stock app registered, the system either
    silently picks the stock app or shows a chooser — the first PR #40 CI run
    hit the silent-stock path (pick_chooser: chooser_shown=False, then
    wait_portal_screen timed out). We disable every non-Gatepath handler.

    Requires `adb root` (userdebug emulator images support it; production
    images do not). Best-effort: on a production image we record the outcome
    and continue rather than blocking the run.
    """
    serial = state["serial"]
    root_result = adb_helper.adb(serial, "root", check=False, timeout=15)
    combined = (root_result.stdout + root_result.stderr).lower()
    if root_result.returncode != 0 or "cannot run as root" in combined:
        return {"rooted": False, "disabled": False, "note": "adb root unavailable"}

    # adb root restarts adbd; wait until the *root* daemon is actually serving
    # before issuing pm. A bare wait-for-device races the restart — that race
    # was the original failure: pm ran against a half-restarted adbd and its
    # output was lost, leaving the stock handler enabled.
    if not adb_helper.wait_for_root(serial, timeout=30):
        return {
            "rooted": True,
            "ready": False,
            "disabled": False,
            "note": "root adbd did not come back within 30s",
        }

    # Disable every handler of the sign-in intent except Gatepath, rather than
    # hardcoding one package (robust to extra handlers / image differences).
    handlers = _captive_portal_handlers(serial)
    targets = [p for p in handlers if p != APP_PACKAGE] or [STOCK_HANDLER_PKG]

    pm_results: dict[str, str] = {}
    for pkg in targets:
        out, err = adb_helper.shell_full(
            serial, f"pm disable-user --user 0 {pkg}", timeout=15, check=False
        )
        msg = (out or err).strip()
        if "new state: disabled" not in msg.lower():
            time.sleep(1.0)  # one settle+retry for any residual flakiness
            out, err = adb_helper.shell_full(
                serial, f"pm disable-user --user 0 {pkg}", timeout=15, check=False
            )
            msg = (out or err).strip()
        pm_results[pkg] = msg[:120]

    # Verify against the disabled-package list — the authoritative signal,
    # independent of how pm phrased its stdout.
    disabled_list, _ = adb_helper.shell_full(
        serial, "pm list packages -d", timeout=15, check=False
    )
    disabled_targets = [p for p in targets if p in disabled_list]
    state["disabled_handlers"] = disabled_targets  # for re-enable in teardown

    return {
        "rooted": True,
        "ready": True,
        "handlers": handlers,
        "targets": targets,
        "pm_results": pm_results,
        "disabled": len(disabled_targets) == len(targets) and bool(targets),
        "disabled_targets": disabled_targets,
    }


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
    """Open the notification shade and tap the captive 'Sign in' notification.

    With the stock handler disabled (step_disable_stock_handler), the
    system auto-dispatches the CAPTIVE_PORTAL intent directly to
    CaptivePortalActivity — no user tap required. In that case the
    notification may not exist at all, or wait_portal_screen will see
    the activity start before tap_notification can find anything.
    Either way, missing the notification is NOT a failure.
    """
    serial = state["serial"]
    # If the CaptivePortalActivity is already running (auto-dispatch path),
    # skip the notification dance entirely.
    fg = adb_helper.shell(serial, "dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivityRecord' | head -3", timeout=10, check=False)
    if APP_PACKAGE in fg and "CaptivePortalActivity" in fg:
        return {"auto_dispatched": True, "tapped": False}
    adb_helper.shell(serial, "cmd statusbar expand-notifications", timeout=10)
    time.sleep(1.5)
    for fragment in ("Sign in", "Wi-Fi"):
        try:
            node = uiautomator_helper.wait_for_text_contains(serial, fragment, timeout=8)
        except RuntimeError:
            continue
        x, y = uiautomator_helper.midpoint(node)
        uiautomator_helper.tap(serial, x, y)
        # Close the shade so it doesn't shadow follow-up UI dumps.
        adb_helper.shell(serial, "cmd statusbar collapse", timeout=5, check=False)
        return {"tapped_at": [x, y], "tapped": True, "matched": fragment}
    # Notification not found — fine if system already auto-dispatched.
    adb_helper.shell(serial, "cmd statusbar collapse", timeout=5, check=False)
    return {"tapped": False, "note": "no Sign-in notification found; expecting auto-dispatch"}


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


def _capture_dispatch_diagnostics(state: dict) -> dict:
    """On a wait_portal_screen timeout, record what actually handled the
    CAPTIVE_PORTAL intent. This step short-circuits the scenario, so the
    dedicated pull_logcat step never runs — without this, a failed CI run
    gives no clue whether the intent went to the stock app, a chooser, or
    nowhere. Defensive: never raises (it runs on the failure path)."""
    serial = state["serial"]
    diag: dict[str, str] = {}
    try:
        fg, _ = adb_helper.shell_full(
            serial,
            "dumpsys activity activities | "
            "grep -E 'mResumedActivity|topResumedActivityRecord' | head -3",
            timeout=10,
            check=False,
        )
        resolver, _ = adb_helper.shell_full(
            serial,
            f"cmd package resolve-activity --brief -a {CAPTIVE_PORTAL_ACTION} "
            "-c android.intent.category.DEFAULT",
            timeout=10,
            check=False,
        )
        log = adb_helper.shell(serial, "logcat -d -t 400", timeout=15, check=False)
        (state["artifacts_dir"] / "wait_portal_screen-diagnostics.txt").write_text(
            f"foreground:\n{fg}\n\nresolver:\n{resolver}\n\nlogcat tail:\n{log}\n"
        )
        diag = {"foreground": fg.strip()[:200], "resolver": resolver.strip()[:200]}
    except Exception as e:  # noqa: BLE001 — diagnostics must not mask the timeout
        diag = {"diag_error": f"{type(e).__name__}: {e}"}
    return diag


def step_wait_portal_screen(state: dict) -> dict:
    serial = state["serial"]
    # GatepathCaptive: "Handling captive portal for network ..." per
    # CaptivePortalActivity.kt:87. Equivalent: PortalScreen's "Network
    # Sign-In" title via UI dump. Watch logcat — cheaper, less flaky.
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        log = adb_helper.shell(serial, "logcat -d -t 200 -s GatepathCaptive:I", timeout=10)
        if "Handling captive portal" in log:
            return {"detected_in_logcat": True}
        time.sleep(1.5)
    diag = _capture_dispatch_diagnostics(state)
    raise RuntimeError(
        "CaptivePortalActivity did not start within 45s; "
        f"foreground={diag.get('foreground')!r} resolver={diag.get('resolver')!r}"
    )


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


def step_enable_stock_handler(state: dict) -> dict:
    """Re-enable whatever the disable step turned off, leaving the device in a
    clean state. Best-effort; falls back to the known stock package if the
    disable step recorded nothing (e.g. root was unavailable)."""
    serial = state["serial"]
    targets = state.get("disabled_handlers") or [STOCK_HANDLER_PKG]
    pm_results: dict[str, str] = {}
    for pkg in targets:
        out, err = adb_helper.shell_full(
            serial, f"pm enable --user 0 {pkg}", timeout=15, check=False
        )
        pm_results[pkg] = (out or err).strip()[:120]
    return {"targets": targets, "pm_results": pm_results}


def step_disconnect(state: dict) -> dict:
    adb_helper.disconnect(state["emulator_addr"])
    return {"disconnected": True}


STEPS: list[Callable[[dict], dict]] = [
    step("connect", step_connect),
    step("reset_settings", step_reset_settings),
    step("install", step_install),
    step("disable_stock_handler", step_disable_stock_handler),
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
    step("enable_stock_handler", step_enable_stock_handler),
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
        # Always capture logcat. The scenario short-circuits on the first
        # failing step, so the dedicated pull_logcat step (near the end) never
        # runs on a mid-scenario failure — without this a failed CI run yields
        # no device logs to diagnose from.
        serial = state.get("serial")
        if serial:
            try:
                log = adb_helper.shell(
                    serial, "logcat -d -t 3000", timeout=20, check=False
                )
                (state["artifacts_dir"] / "logcat.txt").write_text(log)
            except Exception:  # noqa: BLE001 — diagnostics must never mask the rc
                pass
        report_path = state["artifacts_dir"] / "scenario-report.json"
        report_path.write_text(json.dumps(report, indent=2))
        print(f"\nwrote {report_path}")

    return report["rc"]


if __name__ == "__main__":
    sys.exit(main())
