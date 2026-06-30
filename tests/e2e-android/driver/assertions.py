#!/usr/bin/env python3
"""Host-side assertions for the Gatepath Android e2e harness.

Runs AFTER the scenario completes. Reads three artefacts from the
directory passed on argv:

    scenario-report.json   — written by run-scenario.py
    audit_log.jsonl        — pulled from /data/data/cc.grepon.gatepath/files/
    gateway-log.json       — fetched from mockportal's /log endpoint

Three buckets, all hard-fail:

  A. Scenario report  — every step ok, rc=0, key step outputs sane.
  B. App audit log    — at least one Completed entry with reason
                        'portal_completed' (PR #33 close reason).
  C. Gateway log      — /portal was requested from an Android UA AND no
                        off-domain hostnames appeared in any Host header.
                        The off-domain block is the most-load-bearing
                        security claim Gatepath makes; if it leaks, the
                        test must fail hard.

Exit 0 only if every check passes. Mirrors tests/e2e-docker/driver/assertions.py
in tone, layout, and exit semantics.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

OFF_DOMAIN_HOSTNAMES = frozenset(
    {
        "evil-tracker.example.com",
        "external-site.example.com",
    }
)

SENTINEL_IP = "203.0.113.7"

EXPECTED_STEPS = [
    "connect",
    "reset_settings",
    "install",
    "reset_gateway",
    "set_probe_urls",
    "cycle_wifi",
    "wait_for_captive",
    "grant_vpn",
    "start_test_vpn",
    "liveness_probe",
    "launch_debug_portal",
    "wait_portal_screen",
    "submit_login",
    "wait_validated",
    "mark_bound_end",
    "pull_vpn_sink",
    "pull_logcat",
    "pull_audit_log",
    "fetch_gateway_log",
    "cleanup_settings",
    "disconnect",
]


def fail(label: str, msg: str, failures: list[str]) -> None:
    failures.append(f"{label}: {msg}")
    print(f"  ✗ {label}: {msg}", file=sys.stderr)


def ok(label: str, msg: str = "") -> None:
    print(f"  ✓ {label}{(' — ' + msg) if msg else ''}")


def check_scenario(report: dict[str, Any], failures: list[str]) -> None:
    print("A. Scenario report")
    if report.get("rc") != 0:
        fail("scenario.rc", f"expected 0, got {report.get('rc')}", failures)
    else:
        ok("scenario.rc", "0")

    steps = {s["name"]: s for s in report.get("steps", [])}
    for name in EXPECTED_STEPS:
        s = steps.get(name)
        if s is None:
            fail(f"scenario.{name}", "step missing from report", failures)
            continue
        if not s.get("ok"):
            fail(f"scenario.{name}", f"step failed: {s.get('error')}", failures)
            continue
        ok(f"scenario.{name}", _summarise(s.get("data") or {}))

    # Spot-checks on key outputs.
    connect = steps.get("connect", {}).get("data", {})
    if not connect.get("serial"):
        fail("scenario.connect.serial", "empty serial", failures)

    probes = steps.get("set_probe_urls", {}).get("data", {})
    if not probes.get("probe_url"):
        fail("scenario.probe.url", "no probe_url recorded", failures)

    validated = steps.get("wait_validated", {}).get("data", {})
    if not isinstance(validated.get("validated_in_sec"), int):
        fail("scenario.validated", "no validated_in_sec recorded", failures)


def check_app_audit(entries: list[dict[str, Any]], failures: list[str]) -> None:
    print("B. App audit log")
    if not entries:
        fail("audit.entries", "audit log empty — session never completed?", failures)
        return
    ok("audit.entries", f"{len(entries)} entries")

    # Look for a Completed entry with close_reason == 'portal_completed'.
    # Audit schema: { ..., 'close_reason': 'portal_completed', ... } per
    # docs/audit_log_schema.json + PR #33 semantics.
    completed = [
        e for e in entries
        if e.get("close_reason") == "portal_completed"
    ]
    if completed:
        ok("audit.portal_completed", f"{len(completed)} entry/entries")
    else:
        fail(
            "audit.portal_completed",
            f"no entry with close_reason='portal_completed'; "
            f"got reasons: {sorted({e.get('close_reason') for e in entries})}",
            failures,
        )


def check_gateway_log(
    entries: list[dict[str, Any]],
    scenario_report: dict[str, Any],
    failures: list[str],
) -> None:
    print("C. Gateway request log")
    if not entries:
        fail("gateway.entries", "gateway log empty — WebView never connected?", failures)
        return
    ok("gateway.entries", f"{len(entries)} entries")

    # /portal must have been requested by an Android-shaped UA.
    portal_hits = [
        e for e in entries
        if e.get("path", "").startswith("/portal")
        and "Android" in (e.get("headers") or {}).get("User-Agent", "")
    ]
    if portal_hits:
        ok("gateway.portal_hit", f"{len(portal_hits)} hit(s) from Android UA")
    else:
        fail(
            "gateway.portal_hit",
            "no /portal request from an Android UA in the gateway log",
            failures,
        )

    # The off-domain block is the security-load-bearing assertion.
    leaks = []
    for e in entries:
        host = (e.get("headers") or {}).get("Host", "")
        host_only = host.split(":", 1)[0].strip().lower()
        if host_only in OFF_DOMAIN_HOSTNAMES:
            leaks.append({"path": e.get("path"), "host": host})
    if leaks:
        fail(
            "gateway.off_domain_blocked",
            f"off-domain hostnames leaked into the gateway: {leaks}",
            failures,
        )
    else:
        ok("gateway.off_domain_blocked", "no off-domain requests observed")


def check_vpn_confinement(lines: list[dict[str, Any]], failures: list[str]) -> None:
    """D. The network-level no-leak proof over the VPN sink (ROADMAP P0.1).

    The bound window is delimited by 'bound_begin'/'bound_end' marker lines the
    test VpnService wrote into the sink (append-order, so no host/device clock
    comparison is needed). D1 (liveness) must hold before D2 (confinement) means
    anything: if the sink never saw the unbound probe it is not intercepting the
    default route, and a silent bound window is vacuous.
    """
    print("D. VPN sink (no-leak confinement)")
    begin = next((i for i, e in enumerate(lines) if e.get("marker") == "bound_begin"), None)
    end = next((i for i, e in enumerate(lines) if e.get("marker") == "bound_end"), None)
    if begin is None or end is None:
        fail("vpn.markers", f"missing bound-window markers (begin={begin}, end={end})", failures)
        return
    if end < begin:
        fail("vpn.markers", f"bound_end ({end}) precedes bound_begin ({begin})", failures)
        return

    # D1 — liveness gate: a sentinel packet must appear BEFORE bound_begin.
    pre = [e for e in lines[:begin] if e.get("dst") == SENTINEL_IP]
    if not pre:
        fail(
            "vpn.liveness",
            "the VPN sink never captured the unbound probe to the sentinel — the "
            "sink is not intercepting the default route, so a silent bound window "
            "proves nothing",
            failures,
        )
        return
    ok("vpn.liveness", f"{len(pre)} unbound sentinel packet(s) captured")

    # D2 — confinement: the bound window must be packet-silent.
    leaks = [e for e in lines[begin + 1:end] if "dst" in e]
    if leaks:
        s = leaks[0]
        fail(
            "vpn.confinement",
            f"LEAK: bound-phase Gatepath traffic to {s.get('dst')}:{s.get('port')} "
            f"escaped onto the default (VPN) network ({len(leaks)} packet(s))",
            failures,
        )
    else:
        ok("vpn.confinement", "bound window silent — traffic confined to WiFi")


def _summarise(data: dict[str, Any]) -> str:
    parts = []
    for k, v in data.items():
        if isinstance(v, (int, str, bool, float)) and len(str(v)) <= 64:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: assertions.py <artifacts-dir>", file=sys.stderr)
        return 2

    root = Path(argv[1])
    failures: list[str] = []

    scenario_path = root / "scenario-report.json"
    if not scenario_path.exists():
        print(f"scenario-report.json missing in {root}", file=sys.stderr)
        return 1
    report = json.loads(scenario_path.read_text())
    check_scenario(report, failures)

    audit_path = root / "audit_log.jsonl"
    if not audit_path.exists() or audit_path.stat().st_size == 0:
        failures.append("audit.file: audit_log.jsonl missing or empty")
        print(f"  ✗ audit_log.jsonl missing or empty in {root}", file=sys.stderr)
    else:
        entries = [
            json.loads(line)
            for line in audit_path.read_text().splitlines()
            if line.strip()
        ]
        check_app_audit(entries, failures)

    gateway_path = root / "gateway-log.json"
    if not gateway_path.exists():
        failures.append("gateway.file: gateway-log.json missing")
        print(f"  ✗ gateway-log.json missing in {root}", file=sys.stderr)
    else:
        entries = json.loads(gateway_path.read_text())
        check_gateway_log(entries, report, failures)

    sink_path = root / "vpn-sink.jsonl"
    if not sink_path.exists() or sink_path.stat().st_size == 0:
        failures.append("vpn.file: vpn-sink.jsonl missing or empty")
        print(f"  ✗ vpn-sink.jsonl missing or empty in {root}", file=sys.stderr)
    else:
        sink_lines = [
            json.loads(line)
            for line in sink_path.read_text().splitlines()
            if line.strip()
        ]
        check_vpn_confinement(sink_lines, failures)

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for f in failures:
            print(f"  • {f}", file=sys.stderr)
        return 1

    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
