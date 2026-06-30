#!/usr/bin/env python3
"""Gatepath Android emulator captive-portal scenario.

Mirrors tests/e2e-docker/client/run-scenario.py in spirit: each step is a
function returning {"name", "ok", "data", "error"}; all steps aggregated
into scenario-report.json under the artifacts dir.

Step sequence:
    1.  connect            — adb connect + wait for boot_completed=1
    2.  reset_settings     — clear stale captive_portal_* globals
    3.  install            — adb install -r <apk>
    4.  reset_gateway      — POST /reset on the mockportal /log + counter
    5.  set_probe_urls     — settings put global captive_portal_*_url <url>
    6.  cycle_wifi         — svc wifi disable; sleep; svc wifi enable
    7.  wait_for_captive   — poll dumpsys for the CAPTIVE_PORTAL capability
    8.  launch_debug_portal— am start the PR #34 debug intent (deterministic;
                             tapping the system notification is unworkable on a
                             headless emulator — see the step docstring)
    9.  wait_portal_screen — confirm the launch log, then wait for the WebView's
                             /portal GET (Android UA) in the mock's request log
    10. submit_login       — host-post mode: POST /login (authenticates the mock)
    11. wait_validated     — poll dumpsys connectivity for IS_VALIDATED
    12. pull_logcat        — adb logcat -d > artifacts/logcat.txt
    13. pull_audit_log     — run-as cat files/audit.jsonl → artifacts/
    14. fetch_gateway_log  — curl /log → artifacts/gateway-log.json
    15. cleanup_settings   — settings delete captive_portal_*
    16. disconnect         — adb disconnect

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

# Settings.Global keys we override. Restoration deletes the same set.
CAPTIVE_KEYS = (
    "captive_portal_mode",
    "captive_portal_http_url",
    "captive_portal_https_url",
    "captive_portal_fallback_url",
)

# On-device path to Gatepath's audit log, relative to the app's data dir.
# AuditLog.init() (audit/AuditLog.kt) creates AuditLogWriter(File(filesDir,
# "audit.jsonl")), so the file is files/audit.jsonl — NOT audit_log.jsonl, which
# is the artifact name we write host-side. Pulled via `adb shell run-as` since
# the app is debuggable and the file lives in app-private storage.
AUDIT_LOG_RELATIVE = "files/audit.jsonl"
APP_PACKAGE = "cc.grepon.gatepath"
TESTVPN_ACTIVITY = f"{APP_PACKAGE}/.testvpn.TestVpnControlActivity"
VPN_SINK_RELATIVE = "files/vpn-sink.jsonl"
# The unbound liveness probe targets a dedicated sentinel host:port the captive
# monitor never touches (it probes 10.0.2.2:18080), so the bound WebView's
# attempt to reach the same sentinel is unambiguous in the VPN sink. Single
# source of truth — must match the Kotlin probe, the mock's injected URL, and
# the assertion (PR #55).
SENTINEL_DST = "10.0.2.2"
SENTINEL_PORT = 18081


def _testvpn(serial: str, action: str, label: str | None = None) -> None:
    cmd = f"am start -n {TESTVPN_ACTIVITY} --es gatepath.testvpn.action {action}"
    if label:
        cmd += f" --es gatepath.testvpn.label {label}"
    adb_helper.shell(serial, cmd, timeout=20, check=False)


def _pull_sink(serial: str) -> list[dict]:
    """run-as cat the VPN sink and parse it into JSON objects, one per line.
    Blank lines and unparseable lines are skipped (the sink is appended to
    concurrently, so a partial trailing line can appear mid-read)."""
    raw = adb_helper.shell(
        serial, f"run-as {APP_PACKAGE} cat {VPN_SINK_RELATIVE}", timeout=10, check=False
    )
    entries: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return entries


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


def step_grant_vpn(state: dict) -> dict:
    """Pre-authorize the VpnService so establish() needs no consent dialog.
    appops runs as the adb shell uid — no root required on the emulator."""
    adb_helper.shell(
        state["serial"], f"appops set {APP_PACKAGE} ACTIVATE_VPN allow", timeout=10
    )
    return {"granted": "ACTIVATE_VPN"}


def step_start_test_vpn(state: dict) -> dict:
    """Bring up the debug leak-detector VPN as the system default network, then
    wait for the service to log that the TUN is established before returning — a
    fixed sleep raced establish() on slower emulators and false-failed D1."""
    serial = state["serial"]
    _testvpn(serial, "start")
    deadline = time.monotonic() + 20
    established = False
    while time.monotonic() < deadline:
        log = adb_helper.shell(serial, "logcat -d", timeout=15, check=False)
        if "test VPN sink established" in log:
            established = True
            break
        time.sleep(1)
    return {"started": True, "established": established}


def step_liveness_probe(state: dict) -> dict:
    """Poll-until-captured, THEN open the bound window.

    Each pass fires the unbound TCP sentinel probe, settles, and pulls the sink;
    it breaks once an entry with the sentinel dst:port is present. Only then is
    the bound_begin marker laid. This deterministically guarantees the unbound
    sentinel packet is in the sink BEFORE the bound window opens (fixing issue
    #2 — the probe was previously absent / raced). If the sentinel is never
    captured within the window, captured=False surfaces it to the assertion
    rather than silently opening a vacuous bound window."""
    serial = state["serial"]
    deadline = time.monotonic() + 25
    captured = False
    while time.monotonic() < deadline:
        _testvpn(serial, "probe")
        time.sleep(1.5)
        if any(
            e.get("dst") == SENTINEL_DST and e.get("port") == SENTINEL_PORT
            for e in _pull_sink(serial)
        ):
            captured = True
            break
    _testvpn(serial, "mark", label="bound_begin")
    return {
        "sentinel": f"{SENTINEL_DST}:{SENTINEL_PORT}",
        "captured": captured,
        "marked": "bound_begin",
    }


def step_mark_bound_end(state: dict) -> dict:
    """Close the bound window. The portal session is still bound at this point
    (right after validation), so the window spans the whole bound lifetime."""
    _testvpn(state["serial"], "mark", label="bound_end")
    return {"marked": "bound_end"}


def step_pull_vpn_sink(state: dict) -> dict:
    """Pull the VPN sink after mark_bound_end has been issued.

    mark_bound_end dispatches via an async `am start`, so reading the sink
    immediately raced the marker write and missed bound_end (issue #1). Poll up
    to ~10s until the bound_end marker line is present, then write the artifact.
    Always write the last pull (even if bound_end never appears) so the artifact
    stays diagnosable; bound_end_seen surfaces whether the race was won."""
    serial = state["serial"]
    deadline = time.monotonic() + 10
    contents = ""
    bound_end_seen = False
    while True:
        contents = adb_helper.shell(
            serial, f"run-as {APP_PACKAGE} cat {VPN_SINK_RELATIVE}", timeout=10, check=False
        )
        for line in contents.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("marker") == "bound_end":
                    bound_end_seen = True
                    break
            except (json.JSONDecodeError, ValueError):
                continue
        if bound_end_seen or time.monotonic() >= deadline:
            break
        time.sleep(1.0)
    out = state["artifacts_dir"] / "vpn-sink.jsonl"
    out.write_text(contents)
    return {"path": str(out), "size": len(contents), "bound_end_seen": bound_end_seen}


def _foreground(serial: str) -> str:
    """The currently focused window/activity, via dumpsys window. More
    reliable across API levels than grepping `dumpsys activity activities`."""
    out, _ = adb_helper.shell_full(
        serial,
        "dumpsys window 2>/dev/null | grep -E 'mCurrentFocus|mFocusedApp' | head -4",
        timeout=10,
        check=False,
    )
    return out.strip()


def step_launch_debug_portal(state: dict) -> dict:
    """Open PortalScreen deterministically via the PR #34 debug intent.

    Tapping the system captive notification proved unachievable on a headless
    emulator (grouped/collapsed notifications, input tap never registers a
    click, and brute-force tapping ANR'd SystemUI). MainActivity reads
    `gatepath.debug.portal_url` (gated on BuildConfig.DEBUG — CI builds with
    assembleDebug) and pushes the session straight to Active, loading the
    portal URL in the same PortalScreen WebView the real flow uses. Requires an
    active network, which cycle_wifi/wait_for_captive established."""
    serial = state["serial"]
    # The emulator floods logcat with package-optimization spam after boot
    # (~hundreds of lines/sec), which buries our app's lines under any bounded
    # `-t` window and can rotate them out of the ring buffer entirely. Enlarge
    # the buffer and clear it right before launch so wait_portal_screen reads a
    # fresh, app-dominated log.
    adb_helper.shell(serial, "logcat -G 8M", timeout=10, check=False)
    adb_helper.shell(serial, "logcat -c", timeout=10, check=False)
    portal_url = f"{state['mockportal_host_url']}/portal"
    out, err = adb_helper.shell_full(
        serial,
        f"am start -n {APP_PACKAGE}/.MainActivity "
        f"--es gatepath.debug.portal_url '{portal_url}'",
        timeout=20,
        check=False,
    )
    return {"portal_url": portal_url, "am_output": (out or err).strip()[:200]}


def _gateway_has_portal_hit(state: dict) -> bool:
    """True if the mock's request log shows a /portal GET from an Android UA —
    the deterministic signal the PortalScreen WebView loaded the portal, and
    exactly what the gateway.portal_hit assertion checks."""
    try:
        body = _http(f"{state['mockportal_from_host_url']}/log", timeout=5)
        for e in json.loads(body):
            ua = (e.get("headers") or {}).get("User-Agent", "")
            if str(e.get("path", "")).startswith("/portal") and "Android" in ua:
                return True
    except Exception:  # noqa: BLE001 — polled; transient errors are fine
        pass
    return False


def step_wait_portal_screen(state: dict) -> dict:
    """Confirm the debug intent opened PortalScreen.

    MainActivity logs `GatepathMain` "Debug portal intent: opening <url> on
    <net>" (MainActivity.kt:99) once it accepts the extra and forces the
    session Active. If the device had no active network it logs
    "no active network; ignored" instead — surface that distinctly."""
    serial = state["serial"]
    # Confirm the debug intent was accepted (MainActivity logs GatepathMain
    # "Debug portal intent: opening"; "no active network" means it bailed),
    # then wait for the WebView to actually fetch /portal — the mock's request
    # log showing a /portal GET from an Android UA. Waiting for the real load
    # (not just the launch log) verifies the page came up AND ensures it's up
    # before submit_login/wait_validated can tear the session down.
    deadline = time.monotonic() + 60
    accepted = False
    while time.monotonic() < deadline:
        if not accepted:
            log = adb_helper.shell(serial, "logcat -d", timeout=20, check=False)
            if "Debug portal intent: no active network" in log:
                raise RuntimeError("debug portal intent ignored: no active network")
            accepted = "Debug portal intent: opening" in log
        if _gateway_has_portal_hit(state):
            return {"intent_accepted": accepted, "portal_loaded": True}
        time.sleep(2)
    fg = _foreground(serial)
    log = adb_helper.shell(serial, "logcat -d", timeout=20, check=False)
    (state["artifacts_dir"] / "wait_portal_screen-diagnostics.txt").write_text(
        f"intent_accepted={accepted}\nforeground:\n{fg}\n\nfull logcat:\n{log}\n"
    )
    raise RuntimeError(
        f"portal WebView never fetched /portal within 60s "
        f"(intent_accepted={accepted}); foreground={fg[:200]!r}"
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


def _wifi_netid(dump: str) -> str | None:
    """Parse the WIFI network id from `dumpsys connectivity` — it appears as
    `network{NNN}` on the same NetworkAgentInfo line as `ni{WIFI ...}`."""
    for line in dump.splitlines():
        if "ni{WIFI" in line:
            m = re.search(r"network\{(\d+)\}", line)
            if m:
                return m.group(1)
    return None


def step_wait_validated(state: dict) -> dict:
    """Wait for the captive WIFI network to gain NET_CAPABILITY_VALIDATED.

    The mock flips /generate_204 from 302 to 204 after PORTAL_COMPLETE_AFTER=3
    probes, so the network validates once NetworkMonitor re-probes enough. The
    debug path has no CaptivePortal token to force an immediate re-probe (the
    real flow's reportCaptivePortalDismissed), so we (a) give it a generous
    window and (b) best-effort ask the framework to re-evaluate the SAME
    network. We deliberately do NOT cycle wifi: a fresh network validates as
    'never captive', which wouldn't drive the captive->validated transition
    that makes CaptivePortalMonitor emit NetworkValidated and write the
    portal_completed audit entry."""
    serial = state["serial"]
    window = 120
    deadline = time.monotonic() + window
    while time.monotonic() < deadline:
        dump = adb_helper.shell(serial, "dumpsys connectivity", timeout=15)
        for line in dump.splitlines():
            if "ni{WIFI" in line and "IS_VALIDATED" in line and "CAPTIVE_PORTAL" not in line:
                return {"validated_in_sec": int(window - (deadline - time.monotonic()))}
        # submit_login authenticated the mock, so any re-probe now returns 204
        # and the SAME network flips captive->validated. The system re-probes
        # captive networks only slowly on its own, so nudge a re-evaluation of
        # the same network each pass to reach 204 quickly. No-ops if the
        # subcommand is unsupported (we then rely on the slow natural re-probe,
        # which still happens within the window).
        netid = _wifi_netid(dump)
        if netid:
            adb_helper.shell(
                serial, f"cmd connectivity reevaluate {netid}", timeout=10, check=False
            )
        time.sleep(4)
    dump = adb_helper.shell(serial, "dumpsys connectivity", timeout=15, check=False)
    (state["artifacts_dir"] / "wait_validated-diagnostics.txt").write_text(dump)
    raise RuntimeError(f"WIFI network never reached IS_VALIDATED within {window}s")


def step_pull_logcat(state: dict) -> dict:
    serial = state["serial"]
    log = adb_helper.shell(serial, "logcat -d -t 2000", timeout=20)
    out = state["artifacts_dir"] / "logcat.txt"
    out.write_text(log)
    return {"path": str(out), "size": len(log)}


def step_pull_audit_log(state: dict) -> dict:
    serial = state["serial"]
    # The audit entry is appended from a coroutine when NetworkValidated fires,
    # which can land just after wait_validated returns. Poll for non-empty
    # content for a few seconds so we don't race the async write. Debug build is
    # debuggable → run-as works.
    contents = ""
    err: str | None = None
    deadline = time.monotonic() + 10
    while True:
        try:
            contents = adb_helper.shell(
                serial,
                f"run-as {APP_PACKAGE} cat {AUDIT_LOG_RELATIVE}",
                timeout=10,
                check=False,
            )
            err = None
        except Exception as e:  # noqa: BLE001
            contents, err = "", str(e)
        if contents.strip() or time.monotonic() >= deadline:
            break
        time.sleep(1.0)
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
    step("grant_vpn", step_grant_vpn),
    step("start_test_vpn", step_start_test_vpn),
    step("liveness_probe", step_liveness_probe),
    step("launch_debug_portal", step_launch_debug_portal),
    step("wait_portal_screen", step_wait_portal_screen),
    step("submit_login", step_submit_login),
    step("wait_validated", step_wait_validated),
    step("mark_bound_end", step_mark_bound_end),
    step("pull_vpn_sink", step_pull_vpn_sink),
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
        # Always capture logcat. The scenario short-circuits on the first
        # failing step, so the dedicated pull_logcat step (near the end) never
        # runs on a mid-scenario failure — without this a failed CI run yields
        # no device logs to diagnose from.
        serial = state.get("serial")
        if serial:
            try:
                adb_helper.shell(
                    serial,
                    f"am start -n {TESTVPN_ACTIVITY} --es gatepath.testvpn.action stop",
                    timeout=15,
                    check=False,
                )
            except Exception:  # noqa: BLE001 — teardown must never mask the rc
                pass
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
