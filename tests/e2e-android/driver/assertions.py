#!/usr/bin/env python3
"""Host-side assertions for the Gatepath Android e2e harness.

Runs AFTER the scenario completes. Reads three artefacts from the
directory passed on argv:

    scenario-report.json   — written by run-scenario.py
    audit_log.jsonl        — pulled from /data/data/com.ventouxlabs.gatepath/files/
    gateway-log.json       — fetched from mockportal's /log endpoint

Buckets, all hard-fail:

  A. Scenario report  — every step ok, rc=0, key step outputs sane.
  B. App audit log    — at least one Completed entry with reason
                        'portal_completed' (PR #33 close reason).
  C. Gateway log      — /portal was requested from an Android UA.
  D. VPN sink         — the no-leak confinement proof (ROADMAP P0.1),
                        gated on its own positive control.
  E. Off-domain       — off-domain traffic is ALLOWED and COUNTED, which is
                        what the design claims since #119; and it must
                        actually have been exercised. "Nothing happened" is
                        a failure here, not a pass — see check_off_domain
                        for why the previous version of this could not fail.

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

# The no-leak sentinel: a dedicated host:port the captive monitor never probes
# (it hits 10.0.2.2:18080). The unbound liveness probe and the bound WebView's
# <img> both target it, so they are distinguishable from captive-monitor noise
# in the VPN sink. Single source of truth — must match the Kotlin probe, the
# scenario harness, and the mock's injected URL (PR #55).
SENTINEL_DST = "10.0.2.2"
SENTINEL_PORT = 18081

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

    # Off-domain traffic is asserted separately, in check_off_domain — it needs
    # the audit log and logcat as well as this one, and the old version here
    # could not fail. See that function for the full story.


def _webview_attempted_off_domain(logcat: str) -> bool:
    """Positive control: did the portal WebView actually try an off-domain host?

    True iff a GatepathWebView line names one. Only that tag counts — another
    component echoing the hostname (the harness printing its own config, say)
    is not evidence the WebView tried to load it. Same rule as
    [_webview_attempted_sentinel].
    """
    for line in logcat.splitlines():
        if "GatepathWebView" not in line:
            continue
        if any(host in line for host in OFF_DOMAIN_HOSTNAMES):
            return True
    return False


def check_off_domain(
    gateway_entries: list[dict[str, Any]],
    audit_entries: list[dict[str, Any]],
    logcat: str,
    failures: list[str],
) -> None:
    """E. Off-domain traffic: allowed and COUNTED, never refused.

    What this replaces, and why it had to go:

        if leaks: fail(...)
        else:     ok("gateway.off_domain_blocked", "no off-domain requests observed")

    That assertion could not fail, for three independent reasons at once —
    confirmed against the artifacts of the last green run, not inferred:

      1. It encoded PREVENTION. Since #119 (navigations) and by original design
         (subresources), both platforms ALLOW off-domain traffic and merely
         count it — blocking cancelled the cross-host sign-in POST that Meraki
         / Cisco ISE / UniFi require, and empty-200'ing GA/GTM broke the portal
         page's own Continue button. So the `leaks` branch would fail on
         CORRECT behaviour, and only the vacuous branch could ever pass.
      2. Its pass branch fires precisely when nothing happened. A WebView that
         never attempted any off-domain request produces an empty `leaks` and
         reads as ✓.
      3. Nothing in the harness makes off-domain traffic happen. Neither
         hostname resolves in the emulator, `evil-tracker.example.com` is not
         in BlockedDomains so no counter fires for it, and the default
         `host-post` login mode submits the form from the host rather than
         navigating the WebView anywhere.

    Last green run's artifacts: `blocked_navigation_attempts` 0,
    `blocked_resource_requests` 0, zero off-domain hosts in the gateway log,
    zero mentions in logcat. Three signals, all silent, reported as a pass.

    So this asserts on EVIDENCE, and treats the absence of evidence as a
    failure rather than a pass:

      * gateway hit  — the request reached the CAPTIVE gateway, which is what
        confinement looks like (the trusted-side half is section D's sentinel)
      * audit counter — onBlockedNavigation() fires in shouldOverrideUrlLoading
        BEFORE the request goes out, so a non-zero counter is real evidence
        even when the host does not resolve
      * logcat        — the WebView's own log naming the host

    No signal at all ⇒ `off_domain.not_exercised`, hard fail: the claim is
    unproven, which is not the same as satisfied.
    """
    print("E. Off-domain traffic (allowed + counted)")

    seen_at_gateway = []
    for e in gateway_entries:
        host = (e.get("headers") or {}).get("Host", "")
        if host.split(":", 1)[0].strip().lower() in OFF_DOMAIN_HOSTNAMES:
            seen_at_gateway.append({"path": e.get("path"), "host": host})

    counted = 0
    for e in audit_entries:
        for field in ("blocked_navigation_attempts", "blocked_resource_requests"):
            value = e.get(field)
            if isinstance(value, int) and value > 0:
                counted += value

    attempted = _webview_attempted_off_domain(logcat)

    if not (seen_at_gateway or counted or attempted):
        fail(
            "off_domain.not_exercised",
            "no evidence of ANY off-domain activity: nothing reached the "
            "gateway, both audit counters are 0, and the WebView never logged "
            "an off-domain host. The off-domain claim is UNPROVEN by this run "
            "— it is not passing, it simply never happened. See issue #120.",
            failures,
        )
        return

    ok(
        "off_domain.exercised",
        f"gateway={len(seen_at_gateway)} counted={counted} webview_logged={attempted}",
    )

    # The #119 regression guard: having attempted off-domain traffic, the app
    # must not have refused it. A refusal shows up as an attempt that produced
    # neither a counted event nor a gateway hit.
    if attempted and not counted and not seen_at_gateway:
        fail(
            "off_domain.allowed",
            "the WebView attempted an off-domain host but nothing was counted "
            "and nothing reached the gateway — the signature of refusing "
            "off-domain traffic again, which breaks cross-host sign-in on "
            "Meraki / Cisco ISE / UniFi (see #119)",
            failures,
        )
    else:
        ok("off_domain.allowed", "off-domain traffic was allowed, not refused")


def _webview_attempted_sentinel(logcat: str) -> bool:
    """D2 positive control: did the portal WebView actually try to reach the
    sentinel? True iff a GatepathWebView line names the sentinel host:port (an
    onReceivedError for the injected <img>, like the evil-tracker one). Without
    this, 'sentinel absent from the VPN sink' is ambiguous — it could mean
    CONFINED, or that the portal page never loaded the sentinel <img> at all (a
    vacuous pass). The unbound probe (logged by GatepathTestVpnCtl) names the
    same host:port and is deliberately NOT counted — only the WebView's own log."""
    needle = f"{SENTINEL_DST}:{SENTINEL_PORT}"
    for line in logcat.splitlines():
        if needle in line and "GatepathWebView" in line:
            return True
    return False


def check_vpn_confinement(
    lines: list[dict[str, Any]], failures: list[str], sentinel_attempted: bool
) -> None:
    """D. The network-level no-leak proof over the VPN sink (ROADMAP P0.1).

    The bound window is delimited by 'bound_begin'/'bound_end' marker lines the
    test VpnService wrote into the sink (append-order, so no host/device clock
    comparison is needed). D1 (liveness) must hold before D2 (confinement) means
    anything: if the sink never saw the unbound probe it is not intercepting the
    default route, and a silent bound window is vacuous. D2 additionally requires
    `sentinel_attempted` (the WebView actually tried the sentinel) so a silent
    window can't pass when the page simply never loaded the sentinel <img>.
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

    # D1 — liveness gate: an unbound sentinel packet (dst:port) must appear
    # BEFORE bound_begin, proving the sink intercepts the default route.
    pre = [
        e for e in lines[:begin]
        if e.get("dst") == SENTINEL_DST and e.get("port") == SENTINEL_PORT
    ]
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

    # D2 — confinement: the bound WebView must NOT reach the sentinel via the
    # default (VPN) network. Only the dedicated sentinel port counts — the
    # captive monitor's own probes to 10.0.2.2:18080 are expected unbound noise
    # in this window and MUST be ignored (they are not a Gatepath leak).
    leaks = [
        e for e in lines[begin + 1:end]
        if e.get("dst") == SENTINEL_DST and e.get("port") == SENTINEL_PORT
    ]
    if leaks:
        s = leaks[0]
        fail(
            "vpn.confinement",
            f"LEAK: bound-phase WebView traffic reached the sentinel "
            f"{s.get('dst')}:{s.get('port')} via the default (VPN) network "
            f"({len(leaks)} packet(s))",
            failures,
        )
    elif not sentinel_attempted:
        # Positive control failed: the sink is clean, but there's no evidence the
        # WebView ever tried the sentinel, so "clean" can't be read as confined.
        fail(
            "vpn.confinement",
            f"inconclusive: no evidence the portal WebView attempted the sentinel "
            f"{SENTINEL_DST}:{SENTINEL_PORT} (no GatepathWebView error for it in "
            f"logcat) — a clean bound window cannot confirm confinement vs. a "
            f"portal that never loaded the sentinel <img>",
            failures,
        )
    else:
        ok(
            "vpn.confinement",
            "WebView attempted the sentinel but it never reached the VPN sink — "
            "confined to WiFi",
        )


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
    # Read once, up front: sections D and E both need logcat, and E needs the
    # audit and gateway entries the sections below parse for their own use.
    logcat_path = root / "logcat.txt"
    logcat_text = (
        logcat_path.read_text(errors="replace") if logcat_path.exists() else ""
    )
    audit_entries: list[dict[str, Any]] = []
    gateway_entries: list[dict[str, Any]] = []

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
        audit_entries = [
            json.loads(line)
            for line in audit_path.read_text().splitlines()
            if line.strip()
        ]
        check_app_audit(audit_entries, failures)

    gateway_path = root / "gateway-log.json"
    if not gateway_path.exists():
        failures.append("gateway.file: gateway-log.json missing")
        print(f"  ✗ gateway-log.json missing in {root}", file=sys.stderr)
    else:
        gateway_entries = json.loads(gateway_path.read_text())
        check_gateway_log(gateway_entries, report, failures)

    # E. Off-domain traffic — needs the gateway log, the audit log AND logcat,
    # so it runs after both have been read. Missing artifacts leave their lists
    # empty, which check_off_domain correctly treats as "no evidence" rather
    # than as a pass.
    check_off_domain(gateway_entries, audit_entries, logcat_text, failures)

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
        # D2 positive control: did the WebView actually attempt the sentinel?
        sentinel_attempted = _webview_attempted_sentinel(logcat_text)
        check_vpn_confinement(sink_lines, failures, sentinel_attempted)

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for f in failures:
            print(f"  • {f}", file=sys.stderr)
        return 1

    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
