#!/usr/bin/env python3
"""Host-side assertions for the gatepath E2E harness.

Runs AFTER the compose stack has come down. Reads three artefacts from
the directory passed on argv:

    scenario-report.json   — written by run-scenario.py inside the client
    helper-audit.jsonl     — copied from /var/lib/gatepath by the entrypoint
    gateway-log.json       — fetched from the captive-gateway /log endpoint

Three buckets of checks, all hard-fail:

  A. Scenario report — every step ok, overall rc=0, expected interface,
     non-empty screenshot, non-zero PID for the launched subprocess.

  B. Helper audit log — at least one successful SetupCaptive, one
     successful LaunchPortal, one successful Teardown (or auto-teardown).
     No unexplained refusals.

  C. Gateway log — the captive client hit /portal (WebView actually
     loaded the portal page) AND the off-domain traffic it generated
     (evil-tracker.example.com, external-site.example.com) was served by
     the CAPTIVE GATEWAY. Off-domain requests are allowed and counted by
     design on both platforms; the security claim is that they stay
     confined to the netns, not that they never happen. The trusted-side
     half of that claim is section D's sentinel probe.

Exit 0 only if every check passes. Exit 1 otherwise, with the failure
list printed to stderr.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

OFF_DOMAIN_HOSTNAMES = frozenset({
    "evil-tracker.example.com",
    "external-site.example.com",
})


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
    expected = ["reset_gateway", "probe", "snapshot_gateway_log",
                "sentinel_baseline", "nm_lookup", "helper_connect", "setup",
                "launch", "dwell_and_screenshot", "netns_confinement", "kill",
                "teardown", "audit_check"]
    for name in expected:
        s = steps.get(name)
        if s is None:
            fail(f"scenario.{name}", "step missing from report", failures)
            continue
        if not s.get("ok"):
            fail(f"scenario.{name}", f"step failed: {s.get('error')}", failures)
            continue
        ok(f"scenario.{name}", _summarise(s.get("data") or {}))

    # Spot-checks on key step outputs.
    probe = steps.get("probe", {}).get("data", {})
    if probe.get("status") != "portal":
        fail("scenario.probe.status", f"expected portal, got {probe.get('status')}", failures)
    if not probe.get("portal_url"):
        fail("scenario.probe.portal_url", "empty portal_url", failures)

    nm = steps.get("nm_lookup", {}).get("data", {})
    if nm.get("interface") != "wlan0":
        fail("scenario.nm.interface", f"expected wlan0, got {nm.get('interface')}", failures)

    setup = steps.get("setup", {}).get("data", {})
    if setup.get("netns_path") != "/var/run/netns/gatepath":
        fail("scenario.setup.netns_path",
             f"expected /var/run/netns/gatepath, got {setup.get('netns_path')}",
             failures)

    launch = steps.get("launch", {}).get("data", {})
    if not isinstance(launch.get("pid"), int) or launch.get("pid", 0) <= 0:
        fail("scenario.launch.pid", f"expected positive pid, got {launch.get('pid')}", failures)

    shot = steps.get("dwell_and_screenshot", {}).get("data", {})
    if not shot.get("screenshot_size"):
        fail("scenario.screenshot", "xwd file empty or missing", failures)


def check_helper_audit(entries: list[dict[str, Any]], failures: list[str]) -> None:
    print("B. Helper audit log")
    if not entries:
        fail("audit.entries", "audit log empty — helper never ran?", failures)
        return
    ok("audit.entries", f"{len(entries)} entries")

    # Helper audit schema (current shape):
    #   { "action": "setup_captive" | "launch_portal" | "teardown_captive" | …,
    #     "decision": { "kind": "success" } | { "kind": "refused", "reason": … },
    #     "sender": "...", "interface": "...", "pid": ... }
    def success_for(action: str) -> bool:
        for e in entries:
            if e.get("action") != action:
                continue
            if (e.get("decision") or {}).get("kind") == "success":
                return True
        return False

    for action in ("setup_captive", "launch_portal", "teardown_captive"):
        if success_for(action):
            ok(f"audit.{action}", "success recorded")
        else:
            fail(f"audit.{action}", "no success entry found", failures)

    # Refusals are fine *if* they carry a reason — that's the whole
    # point of the typed RefusalReason. Catch any that don't.
    for e in entries:
        decision = e.get("decision") or {}
        if decision.get("kind") == "refused" and not decision.get("reason"):
            fail("audit.refusal", f"refusal without reason: {e}", failures)


def check_gateway_log(entries: list[dict[str, Any]], scenario_report: dict[str, Any],
                      failures: list[str]) -> None:
    print("C. Gateway request log")
    if not entries:
        fail("gateway.entries", "gateway log empty — WebView never connected?", failures)
        return
    ok("gateway.entries", f"{len(entries)} entries")

    # A dead WebView subprocess used to wave this whole section through, on the
    # grounds that the stripped container env (no session bus, no GNOME
    # services) kills WebKit's renderer and that the pipeline downstream of the
    # spawn is what's under test.
    #
    # That excuse is stale, and it was load-bearing in the wrong direction.
    # check_gateway_log is reached ONLY on the non-skipped path — the
    # privileged, real-PHY substrate whose entire purpose is to run the portal
    # (the veth substrate returns at check_scenario_skipped and never gets
    # here). On that path a dead WebView does not mean "environment couldn't
    # cope", it means the run proved nothing about the portal, and both checks
    # below are unevaluated rather than satisfied.
    #
    # This is exactly how #118 survived: WebView construction failed on WebKit
    # 6.0 *completely*, and the harness reported ✓ twice on its way past. Per
    # CLAUDE.md, a step can succeed without the invariant it was meant to prove
    # actually holding — so "nothing happened" hard-fails here. See #120.
    # Absent signal and false signal are different bugs and must not share a
    # message. `.get(...).get(...)` returns None for a missing step, a renamed
    # step, or a report that never recorded the key — reading that as "the
    # WebView died" would send the next engineer to debug WebKit when the real
    # problem is that the scenario report drifted.
    steps = {s["name"]: s for s in scenario_report.get("steps", [])}
    dwell = steps.get("dwell_and_screenshot")
    if dwell is None or "subprocess_alive" not in (dwell.get("data") or {}):
        fail("gateway.portal_hit",
             "the scenario report has no dwell_and_screenshot.subprocess_alive, "
             "so whether the WebView ran is UNKNOWN and section C cannot be "
             "evaluated. This is harness/report drift, not a WebView bug — fix "
             "run-scenario.py before reading anything into the checks below",
             failures)
        fail("gateway.off_domain_confined",
             "not evaluated: WebView liveness unknown (see gateway.portal_hit)",
             failures)
        return

    if not dwell["data"]["subprocess_alive"]:
        fail("gateway.portal_hit",
             "the WebView subprocess exited before the dwell, so the portal "
             "was never loaded and NOTHING in section C was evaluated. This is "
             "not a pass — it is missing coverage. Check the client container's "
             "WebKit 6.0 startup first (see #118, #120)",
             failures)
        fail("gateway.off_domain_confined",
             "not evaluated: the WebView never ran, so no off-domain traffic "
             "could be generated to judge. The confinement claim is UNPROVEN "
             "by this run, which is not the same as satisfied (#120)",
             failures)
        return

    if any(e.get("path") == "/portal" for e in entries):
        ok("gateway.portal_hit", "/portal was requested by the live WebView")
    else:
        fail("gateway.portal_hit",
             "WebView stayed up but never requested /portal", failures)

    # Off-domain traffic is ALLOWED and COUNTED, not refused — on both
    # platforms and by design. Android stopped refusing off-domain navigation
    # because it cancelled the cross-host sign-in POST that Meraki / Cisco ISE
    # / UniFi depend on; desktop followed. Subresources were never blocked on
    # either platform: returning an empty 200 for GA/GTM made the portal's own
    # inline script throw and killed the Continue button.
    #
    # So the invariant is NOT "these hostnames never appear". It is that this
    # traffic stays CONFINED: it is served by the captive gateway, inside the
    # netns, and never reaches the trusted network. Appearing in THIS log is
    # the evidence of confinement — the gateway is the captive side. The
    # trusted-side half is check_confinement()'s sentinel probe.
    #
    # The failure this can still catch is a regression back to refusing: a
    # live WebView that loaded /portal, whose page references both off-domain
    # hosts, yet produced no off-domain contact at all.
    off_domain_seen = []
    for e in entries:
        host = (e.get("headers") or {}).get("Host", "")
        # Host header may carry a port suffix; normalise.
        host_only = host.split(":", 1)[0].strip().lower()
        if host_only in OFF_DOMAIN_HOSTNAMES:
            off_domain_seen.append({"path": e.get("path"), "host": host})

    # The WebView is known alive here (dead exits above), so the only remaining
    # question is whether the portal page itself loaded. If it did not, there is
    # again nothing to judge — and again that is missing coverage, not a pass.
    if not any(e.get("path") == "/portal" for e in entries):
        fail("gateway.off_domain_confined",
             "not evaluated: the WebView was alive but never loaded /portal, "
             "so the page that references the off-domain hosts never ran. The "
             "confinement claim is UNPROVEN by this run (#120)",
             failures)
    elif off_domain_seen:
        ok("gateway.off_domain_confined",
           f"{len(off_domain_seen)} off-domain request(s) served by the captive "
           f"gateway (allowed + confined, as designed): {off_domain_seen}")
    else:
        fail("gateway.off_domain_confined",
             "the portal page loaded and references evil-tracker.example.com and "
             "external-site.example.com, but NO off-domain request reached the "
             "gateway — something is refusing them again, which breaks cross-host "
             "sign-in on real captive vendors",
             failures)


def check_confinement(report: dict[str, Any], artifacts_root: Path,
                      failures: list[str]) -> None:
    print("D. No-leak confinement (gatepath netns ⇸ trusted net)")
    steps = {s["name"]: s for s in report.get("steps", [])}

    # The host-side baseline must have reached the sentinel; otherwise an
    # in-netns failure proves nothing (the sentinel could just be down).
    base = steps.get("sentinel_baseline")
    if not base or not base.get("ok"):
        fail("confinement.baseline",
             "host could not reach the sentinel — confinement result is "
             f"meaningless: {base.get('error') if base else 'step missing'}", failures)
    elif base.get("data", {}).get("http_code") != 200:
        fail("confinement.baseline", f"sentinel baseline not 200: {base.get('data')}", failures)
    else:
        ok("confinement.baseline", "sentinel reachable from the host (trusted) side")

    # The load-bearing check: the in-netns probe must have FAILED to reach the
    # sentinel. Read the runner's artifact directly so this gate stands on its
    # own, not just on the in-container step's self-report.
    probe_path = artifacts_root / "netns-sentinel-probe.json"
    if not probe_path.exists():
        fail("confinement.netns_probe",
             f"in-netns probe artifact missing at {probe_path}", failures)
        return
    try:
        probe = json.loads(probe_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        fail("confinement.netns_probe", f"unreadable probe artifact: {exc}", failures)
        return

    if probe.get("reachable") is True:
        fail("confinement.no_leak",
             f"LEAK: the gatepath netns reached the trusted-net sentinel: {probe}",
             failures)
    elif probe.get("reachable") is False:
        ok("confinement.no_leak",
           f"in-netns probe could not reach the sentinel (curl_rc={probe.get('curl_rc')})")
    else:
        fail("confinement.no_leak",
             f"probe artifact has no clear reachable verdict: {probe}", failures)


def check_scenario_skipped(report: dict[str, Any], failures: list[str]) -> None:
    print("A. Scenario report (privileged path SKIPPED — no Wi-Fi PHY)")
    if report.get("rc") != 0:
        fail("scenario.rc", f"expected 0, got {report.get('rc')}", failures)
    else:
        ok("scenario.rc", "0")

    steps = {s["name"]: s for s in report.get("steps", [])}
    # Everything up to (and including) the explicit skip marker must have run.
    required = ["reset_gateway", "probe", "snapshot_gateway_log",
                "sentinel_baseline", "nm_lookup", "helper_connect",
                "privileged_path"]
    for name in required:
        s = steps.get(name)
        if s is None:
            fail(f"scenario.{name}", "step missing from report", failures)
        elif not s.get("ok"):
            fail(f"scenario.{name}", f"step failed: {s.get('error')}", failures)
        else:
            ok(f"scenario.{name}", _summarise(s.get("data") or {}))

    pp = steps.get("privileged_path", {}).get("data", {})
    if not pp.get("skipped"):
        fail("scenario.privileged_path", "expected skipped=True", failures)


def _summarise(data: dict[str, Any]) -> str:
    # One-line summary for printout; only the small primitives.
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

    # On a veth substrate the scenario records an explicit privileged-path SKIP
    # (no Wi-Fi PHY → no DESK-001 PHY move). Assert only what actually ran; the
    # full audit/gateway/confinement gates require a real radio (mac80211_hwsim).
    skipped = any(
        s.get("name") == "privileged_path" and (s.get("data") or {}).get("skipped")
        for s in report.get("steps", [])
    )
    if skipped:
        check_scenario_skipped(report, failures)
        base = next((s for s in report.get("steps", [])
                     if s.get("name") == "sentinel_baseline"), None)
        if not (base and base.get("ok")):
            fail("confinement.baseline", "host could not reach the sentinel", failures)
        else:
            ok("confinement.baseline", "sentinel reachable from the host (trusted) side")
        print("\nNOTE: no-leak confinement DEFERRED — needs a real Wi-Fi PHY "
              "(mac80211_hwsim / hardware). See docs/ROADMAP.md P0.1/P0.2.")
    else:
        check_scenario(report, failures)

        audit_path = root / "helper-audit.jsonl"
        if not audit_path.exists():
            failures.append("audit.file: helper-audit.jsonl missing")
            print(f"  ✗ helper-audit.jsonl missing in {root}", file=sys.stderr)
        else:
            entries = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
            check_helper_audit(entries, failures)

        gateway_path = root / "gateway-log.json"
        if not gateway_path.exists():
            failures.append("gateway.file: gateway-log.json missing")
            print(f"  ✗ gateway-log.json missing in {root}", file=sys.stderr)
        else:
            entries = json.loads(gateway_path.read_text())
            # Reached only on the privileged path. check_gateway_log hard-fails
            # on a dead WebView, which is correct HERE — this substrate exists
            # to load the portal — and would be wrong on any substrate that
            # never runs it. Keep this call inside the non-skipped branch.
            check_gateway_log(entries, report, failures)

        check_confinement(report, root, failures)

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for f in failures:
            print(f"  • {f}", file=sys.stderr)
        return 1

    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
