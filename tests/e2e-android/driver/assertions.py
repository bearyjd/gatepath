#!/usr/bin/env python3
"""Host-side assertions for the Gatepath Android e2e harness.

Runs AFTER the scenario completes. Reads artefacts from the directory passed
on argv, and takes a `--vpn-mode {covering,excluding}` matching the scenario
run (Task 14; see scenario/run-scenario.py's STEPS_COVERING/STEPS_EXCLUDING):

    scenario-report.json    — written by run-scenario.py
    confinement-state.txt   — the app's classified state (both modes)
    audit_log.jsonl         — pulled from /data/data/com.ventouxlabs.gatepath/files/
    gateway-log.json        — fetched from mockportal's /log endpoint
    vpn-sink.jsonl          — the leak-detector VPN's captured packets + markers
    logcat.txt              — full post-clear device log
    diagnostics-bundle.txt  — the redacted bundle the app can share

Buckets, all hard-fail. Which run depends on `--vpn-mode`:

  A.  Scenario report  — every step ok, rc=0, key step outputs sane. The
                         expected step list is mode-specific (imported from
                         run-scenario.py by name, not hand-duplicated).
  G.  Confinement state — the app classified the state the mode requires.
  B.  App audit log (excluding) — at least one Completed entry with reason
                         'portal_completed' (PR #33 close reason).
  B.  Audit absent (covering)   — NO portal_completed entry; a Tunnelled
                         session never opens one, so an empty/missing audit
                         log is a PASS here, not a failure.
  B'. Audit confinement (excluding) — every portal_completed entry carries
                         confinement == 'confined' (schema v2).
  C.  Gateway log (excluding)   — /portal was requested from an Android UA.
  C'. Gateway silent (covering) — /portal was NEVER requested: no WebView
                         ever opens while Tunnelled.
  D.  VPN sink (covering only) — the no-leak confinement proof (ROADMAP
                         P0.1): the bound window must stay silent. Not run in
                         excluding mode at all — STEPS_EXCLUDING never lays a
                         bound_begin/bound_end marker pair (Gatepath itself
                         is EXCLUDED from the VPN there, so its own traffic
                         never rides the tunnel the sink watches), so the
                         sink is pulled purely for the record in that mode,
                         not asserted on. See check_vpn_silent_while_tunnelled.
  E.  Off-domain (excluding only) — off-domain traffic is ALLOWED and
                         COUNTED, which is what the design claims since #119;
                         and it must actually have been exercised. "Nothing
                         happened" is a failure here, not a pass — see
                         check_off_domain for why the previous version of
                         this could not fail. Not run in covering mode: no
                         WebView ever opens there.
  F.  Diagnostics bundle (both modes) — the shared artefact; both modes
                         require a `confinement: ` field, only excluding
                         mode requires the cleared-capture and redaction
                         checks (covering never validates anything, and its
                         audit log — see B above — is always empty, so there
                         is no PII in the picture to prove was scrubbed).

Exit 0 only if every check passes. Mirrors tests/e2e-docker/driver/assertions.py
in tone, layout, and exit semantics.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# Load the scenario module by path, not `import` — driver/ and scenario/ are
# separate non-package directories. Mirrors
# scenario/test_liveness_probe_drain.py's own loading of run-scenario.py.
# scenario/ is inserted onto sys.path FIRST so run-scenario.py's own `import
# adb_helper` resolves regardless of whether pytest already put it there
# (running `pytest driver scenario` together) or not (`pytest driver` alone).
_SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenario"
if str(_SCENARIO_DIR) not in sys.path:
    sys.path.insert(0, str(_SCENARIO_DIR))
_scenario_spec = importlib.util.spec_from_file_location(
    "run_scenario", _SCENARIO_DIR / "run-scenario.py"
)
run_scenario = importlib.util.module_from_spec(_scenario_spec)
_scenario_spec.loader.exec_module(run_scenario)

OFF_DOMAIN_HOSTNAMES = frozenset(
    {
        "evil-tracker.example.com",
        "external-site.example.com",
    }
)

# The no-leak sentinel: a dedicated host:port the captive monitor never probes
# (it hits 10.0.2.2:18080). The unbound liveness probe and the bound WebView's
# <img> both target it, so they are distinguishable from captive-monitor noise
# in the VPN sink. Single source of truth lives in run-scenario.py now that
# this module loads it directly — must still match the Kotlin probe and the
# mock's injected URL (PR #55), but there is no longer a second hand-copied
# constant here to drift out of sync with the scenario harness.
SENTINEL_DST = run_scenario.SENTINEL_DST
SENTINEL_PORT = run_scenario.SENTINEL_PORT


def fail(label: str, msg: str, failures: list[str]) -> None:
    failures.append(f"{label}: {msg}")
    print(f"  ✗ {label}: {msg}", file=sys.stderr)


def ok(label: str, msg: str = "") -> None:
    print(f"  ✓ {label}{(' — ' + msg) if msg else ''}")


def check_scenario(
    report: dict[str, Any],
    expected_steps: list[str],
    mode: str,
    failures: list[str],
) -> None:
    """A. Every step in `expected_steps` (the mode's own step list, from
    run-scenario.py's step_names(STEPS_COVERING)/step_names(STEPS_EXCLUDING))
    ran and reported ok, plus spot-checks on a few key outputs."""
    print("A. Scenario report")
    if report.get("rc") != 0:
        fail("scenario.rc", f"expected 0, got {report.get('rc')}", failures)
    else:
        ok("scenario.rc", "0")

    steps = {s["name"]: s for s in report.get("steps", [])}
    for name in expected_steps:
        s = steps.get(name)
        if s is None:
            fail(f"scenario.{name}", "step missing from report", failures)
            continue
        if not s.get("ok"):
            fail(f"scenario.{name}", f"step failed: {s.get('error')}", failures)
            continue
        ok(f"scenario.{name}", _summarise(s.get("data") or {}))

    # Spot-checks on key outputs. connect/set_probe_urls are in COMMON_HEAD,
    # so both modes run them.
    connect = steps.get("connect", {}).get("data", {})
    if not connect.get("serial"):
        fail("scenario.connect.serial", "empty serial", failures)

    probes = steps.get("set_probe_urls", {}).get("data", {})
    if not probes.get("probe_url"):
        fail("scenario.probe.url", "no probe_url recorded", failures)

    # wait_validated only exists in STEPS_EXCLUDING — covering mode never
    # signs in, so there is nothing to spot-check here.
    if mode == "excluding":
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


def check_audit_confined(entries: list[dict[str, Any]], failures: list[str]) -> None:
    """B' (excluding). The completed session must carry confinement=confined
    (schema v2) — the excluding-mode contract is that Gatepath signed in
    while CONFINED, not merely that some session closed."""
    print("B'. Audit confinement")
    completed = [e for e in entries if e.get("close_reason") == "portal_completed"]
    if not completed:
        fail("audit.confined", "no portal_completed entry", failures)
        return
    bad = [e.get("confinement") for e in completed if e.get("confinement") != "confined"]
    if bad:
        fail("audit.confined", f"portal_completed entries with confinement={bad}", failures)
    else:
        ok("audit.confined", f"{len(completed)} entry/entries confined")


def check_audit_absent(entries: list[dict[str, Any]], failures: list[str]) -> None:
    """B (covering). A Tunnelled app never opens a session, so it must never
    close one either. An empty or missing audit log is exactly what this mode
    should produce — a PASS here, unlike the 'session never completed?'
    failure [check_app_audit] raises on the same input in excluding mode."""
    print("B. Audit log (no portal_completed)")
    completed = [e for e in entries if e.get("close_reason") == "portal_completed"]
    if completed:
        fail(
            "audit.absent",
            f"{len(completed)} portal_completed entry/entries found while "
            "Tunnelled — a session opened and closed when none should have",
            failures,
        )
    else:
        ok("audit.absent", f"no portal_completed entry ({len(entries)} total entries)")


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


def check_gateway_silent(entries: list[dict[str, Any]], failures: list[str]) -> None:
    """C' (covering). A Tunnelled app must never load the portal: MainViewModel
    classifies TUNNELLED before any session opens, so the mock must never see
    a /portal request from an Android UA at all."""
    print("C'. Gateway must be silent")
    hits = [
        e for e in entries
        if str(e.get("path", "")).startswith("/portal")
        and "Android" in (e.get("headers") or {}).get("User-Agent", "")
    ]
    if hits:
        fail(
            "gateway.silent",
            f"{len(hits)} /portal hit(s) from an Android UA while Tunnelled — "
            "a WebView opened",
            failures,
        )
    else:
        ok("gateway.silent", "no /portal request from an Android UA")


# Chromium error codes that mean "something short-circuited this request before
# the network stack got it" rather than "the network stack tried and failed".
# An app-side refusal produces one of these or no error line at all; it can
# never produce a DNS/TCP/TLS error, because it never gets that far.
APP_SIDE_NET_ERRORS = frozenset({"ERR_BLOCKED_BY_CLIENT", "ERR_ABORTED"})

# Markers that a GatepathWebView line describes a real network-stack outcome for
# the URL it names.
_NETWORK_OUTCOME_MARKERS = ("Page started:", "Page finished:")


def _webview_off_domain_evidence(logcat: str) -> tuple[bool, bool]:
    """Positive control: what did the portal WebView do with an off-domain host?

    Returns ``(attempted, reached_network)``.

    ``attempted`` is True iff a GatepathWebView line names an off-domain host.
    Only that tag counts — another component echoing the hostname (the harness
    printing its own config, say) is not evidence the WebView tried to load it.
    Same rule as [_webview_attempted_sentinel].

    ``reached_network`` is True iff one of those lines shows the request being
    handed to the network stack: a page load for the host, or a ``net::`` error
    that only the network stack can raise (DNS, TCP, TLS). This is the signal
    that distinguishes ALLOWED from REFUSED, and the harness needs it because
    neither off-domain hostname resolves in the emulator and neither is in
    BlockedDomains — so a correctly-allowed request produces no gateway hit and
    no audit counter. ``ERR_NAME_NOT_RESOLVED`` on such a host is a *pass*: the
    app let it out and DNS is what stopped it. See [check_off_domain].
    """
    attempted = False
    reached_network = False
    for line in logcat.splitlines():
        if "GatepathWebView" not in line:
            continue
        if not any(host in line for host in OFF_DOMAIN_HOSTNAMES):
            continue
        attempted = True
        if any(marker in line for marker in _NETWORK_OUTCOME_MARKERS):
            reached_network = True
        elif "net::" in line:
            code = line.split("net::", 1)[1].split()[0].strip()
            if code not in APP_SIDE_NET_ERRORS:
                reached_network = True
    return attempted, reached_network


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

    ALLOWED vs REFUSED is then decided on the network-stack outcome, NOT on the
    absence of a counter. Reason #3 above cuts both ways: because neither
    hostname resolves and neither is in BlockedDomains, a *correctly allowed*
    request reaches no gateway and increments no counter. Its only trace is the
    WebView's own `net::ERR_NAME_NOT_RESOLVED` — which is positive proof of
    allow, since a refusal short-circuits in shouldOverrideUrlLoading /
    shouldInterceptRequest and never reaches DNS. Keying the failure off
    "attempted and not counted" instead flagged that exact line as a refusal;
    see [_webview_off_domain_evidence] for the discriminator that replaces it.
    """
    print("E. Off-domain traffic (allowed + counted)")

    seen_at_gateway = []
    for e in gateway_entries:
        host = (e.get("headers") or {}).get("Host", "")
        if host.split(":", 1)[0].strip().lower() in OFF_DOMAIN_HOSTNAMES:
            seen_at_gateway.append({"path": e.get("path"), "host": host})

    counted = 0
    for e in audit_entries:
        for field in ("observed_navigation_attempts", "observed_resource_requests"):
            value = e.get(field)
            if isinstance(value, int) and value > 0:
                counted += value

    attempted, reached_network = _webview_off_domain_evidence(logcat)

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
        f"gateway={len(seen_at_gateway)} counted={counted} "
        f"webview_logged={attempted} reached_network={reached_network}",
    )

    # The #119 regression guard: having attempted off-domain traffic, the app
    # must not have refused it. Allow leaves one of three traces — the request
    # reached the gateway, an audit counter fired, or the network stack itself
    # reported the outcome. A refusal leaves none of the three, because it
    # short-circuits before any of them can happen.
    if attempted and not (reached_network or counted or seen_at_gateway):
        fail(
            "off_domain.allowed",
            "the WebView named an off-domain host but the request never "
            "reached the network stack, nothing was counted and nothing "
            "reached the gateway — the signature of refusing off-domain "
            "traffic again, which breaks cross-host sign-in on Meraki / "
            "Cisco ISE / UniFi (see #119)",
            failures,
        )
    else:
        ok("off_domain.allowed", "off-domain traffic was allowed, not refused")


def _webview_attempted_sentinel(logcat: str) -> bool:
    """[check_vpn_confinement]'s D2 positive control: did the portal WebView
    actually try to reach the sentinel? True iff a GatepathWebView line names
    the sentinel host:port (an onReceivedError for the injected <img>, like
    the evil-tracker one). Without this, 'sentinel absent from the VPN sink'
    is ambiguous — it could mean CONFINED, or that the portal page never
    loaded the sentinel <img> at all (a vacuous pass). The unbound probe
    (logged by GatepathTestVpnCtl) names the same host:port and is
    deliberately NOT counted — only the WebView's own log.

    Not called from `main()` for the same reason [check_vpn_confinement]
    isn't — see that function's docstring."""
    needle = f"{SENTINEL_DST}:{SENTINEL_PORT}"
    for line in logcat.splitlines():
        if needle in line and "GatepathWebView" in line:
            return True
    return False


def _find_bound_window(
    lines: list[dict[str, Any]], failures: list[str]
) -> tuple[int, int] | None:
    """Locate and order-validate the 'bound_begin'/'bound_end' marker lines
    the test VpnService wrote into the sink (append-order, so no host/device
    clock comparison is needed). Shared by [check_vpn_confinement] and
    [check_vpn_silent_while_tunnelled] — both delimit the same window, only
    what counts as a leak inside it differs."""
    begin = next((i for i, e in enumerate(lines) if e.get("marker") == "bound_begin"), None)
    end = next((i for i, e in enumerate(lines) if e.get("marker") == "bound_end"), None)
    if begin is None or end is None:
        fail("vpn.markers", f"missing bound-window markers (begin={begin}, end={end})", failures)
        return None
    if end < begin:
        fail("vpn.markers", f"bound_end ({end}) precedes bound_begin ({begin})", failures)
        return None
    return begin, end


def _check_liveness(lines: list[dict[str, Any]], begin: int, failures: list[str]) -> bool:
    """D1 — liveness gate: an unbound sentinel packet (dst:port) must appear
    BEFORE bound_begin, proving the sink intercepts the default route. If it
    never does, the sink isn't proven to be watching anything, and a silent
    bound window afterward proves nothing either way."""
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
        return False
    ok("vpn.liveness", f"{len(pre)} unbound sentinel packet(s) captured")
    return True


def check_vpn_confinement(
    lines: list[dict[str, Any]], failures: list[str], sentinel_attempted: bool
) -> None:
    """The network-level no-leak proof over the VPN sink (ROADMAP P0.1), for
    a bound window whose owner DID attempt the sentinel via a WebView.

    NOT called from `main()`: STEPS_EXCLUDING never lays a bound_begin/
    bound_end marker pair (Gatepath is EXCLUDED from the VPN there, so its
    own traffic never rides the tunnel the sink watches), and `covering`
    mode has no WebView to have attempted anything, so it uses
    [check_vpn_silent_while_tunnelled] instead. Kept — with its own direct
    unit tests below — as the general-purpose "was a positive-controlled
    bound window actually leak-free" check, for a future mode that reads the
    sink as this function expects.

    D1 (liveness, [_check_liveness]) must hold before D2 (confinement) means
    anything. D2 additionally requires `sentinel_attempted` (the WebView
    actually tried the sentinel) so a silent window can't pass when the page
    simply never loaded the sentinel <img> — see [check_vpn_silent_while_tunnelled]
    for why `covering` mode doesn't need this same positive control.
    """
    print("D. VPN sink (no-leak confinement)")
    window = _find_bound_window(lines, failures)
    if window is None:
        return
    begin, end = window
    if not _check_liveness(lines, begin, failures):
        return

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


def check_vpn_silent_while_tunnelled(
    lines: list[dict[str, Any]], failures: list[str]
) -> None:
    """D (covering). The no-leak proof for a session that never opens.

    Same D1 liveness gate and bound-window delimiting as
    [check_vpn_confinement], but WITHOUT its D2 positive control
    (`sentinel_attempted`). In `covering` mode there is no WebView to have
    attempted the sentinel at all: MainViewModel classifies TUNNELLED before
    any portal session opens, and the covered probe's own connect() fails
    with EPERM before a single packet leaves the device. So a silent bound
    window here is not ambiguous the way an untried WebView would make it in
    `excluding` mode — there is nothing else that COULD have produced
    sentinel traffic in this window, so silence alone is the confinement
    proof and no positive control is needed to rule out a vacuous pass.
    """
    print("D. VPN sink (silent while tunnelled)")
    window = _find_bound_window(lines, failures)
    if window is None:
        return
    begin, end = window
    if not _check_liveness(lines, begin, failures):
        return

    leaks = [
        e for e in lines[begin + 1:end]
        if e.get("dst") == SENTINEL_DST and e.get("port") == SENTINEL_PORT
    ]
    if leaks:
        s = leaks[0]
        fail(
            "vpn.confinement",
            f"LEAK: bound-phase traffic reached the sentinel "
            f"{s.get('dst')}:{s.get('port')} via the default (VPN) network "
            f"({len(leaks)} packet(s)) while the app was classified TUNNELLED "
            "— EPERM should have stopped every attempt before a packet left "
            "the device",
            failures,
        )
    else:
        ok(
            "vpn.confinement",
            "bound window silent — a TUNNELLED app produced no sentinel "
            "traffic (no WebView exists to attempt it, and the covered "
            "probe's own connect() fails with EPERM before any packet)",
        )


def check_confinement(text: str, mode: str, failures: list[str]) -> None:
    """G. The state the app classified, read from the pulled sidecar
    (files/confinement-state.txt, written by MainViewModel's debug sink on
    every classification — Task 9).

    Absent evidence is a failure: an empty file means the ViewModel never
    classified at all, which is the silent short-circuit this harness exists
    to catch, not a vacuous pass.
    """
    print("G. Confinement state")
    expected = {"covering": "tunnelled", "excluding": "confined"}[mode]
    got = text.strip()
    if not got:
        fail(
            "confinement.file",
            "confinement-state.txt missing or empty — nothing was classified",
            failures,
        )
    elif got != expected:
        fail(
            "confinement.state",
            f"expected {expected!r} in {mode} mode, app classified {got!r}",
            failures,
        )
    else:
        ok("confinement.state", got)


def check_diagnostics_bundle(
    bundle: str,
    audit_entries: list[dict[str, Any]],
    uri: str,
    mode: str,
    failures: list[str],
) -> None:
    """F (both modes). The bundle the user actually shares.

    Everything upstream of this is verified by unit tests against a bundle
    STRING. This is the only pass that looks at a bundle a real device wrote to
    a real FileProvider path, which is where an authority or file_paths.xml
    mistake surfaces and where a unit test cannot reach.

    Written to fail on absent evidence rather than pass quietly: an assertion
    that cannot fail is the defect this driver exists to prevent (#134/#135).

    Extended for the confinement-state harness (Task 15): DiagnosticsBundle.
    renderEvidence renders exactly ONE of two things, never both —
    `confinement: <state>` plus the rest of the incident evidence when
    `_evidence` is non-null, or the literal `(no incident evidence
    captured)` when it is null. Which one a real bundle must show depends on
    mode, so this check is mode-specific rather than a single "field present"
    test that both modes shared before. `excluding` validates, and
    NetworkValidated's `IncidentTracker.clearIf(network)` clears the evidence
    for that same network BEFORE this bundle is pulled (the harness has one
    Wi-Fi network, so it always matches) — so the bundle must show the
    CLEARED prose, and a
    `confinement: ` line here means a previous incident's evidence outlived
    the incident it describes (bundle.evidence_cleared, mirroring
    bundle.capture_cleared below). `covering` never validates anything, so
    nothing ever clears `_evidence` — the bundle must still carry
    `confinement: tunnelled` (bundle.confinement). The capture-cleared AND
    redaction checks below only apply in `excluding` mode for a related
    reason: `covering` mode never opens a session, so there is no probe
    capture to have outlived its incident, and the audit log
    DiagnosticsBundle.redactEntry scrubs is always empty there by design (not
    an edge case this run happened to hit) — there is no PII in the picture
    to prove was or wasn't leaked.
    """
    if not bundle.strip():
        fail("bundle.file", "diagnostics-bundle.txt missing or empty", failures)
        return
    ok("bundle.file", f"{len(bundle)} bytes")

    # The FileProvider URI proves the authority resolved. The app writes it only
    # after getUriForFile returns, so its absence means the share would have
    # thrown there and the user would have seen just "share failed". Read from a
    # pulled file rather than logcat, which is not a dependable channel here.
    uri = uri.strip()
    if not uri:
        fail("bundle.uri", "bundle-uri.txt missing or empty — getUriForFile never returned", failures)
    elif not uri.startswith("content://"):
        fail("bundle.uri", f"expected a content:// URI, got: {uri!r}", failures)
    else:
        ok("bundle.uri", "FileProvider minted a content:// URI")

    # Redaction, checked against the identifiers this run actually produced.
    # `excluding` only: DiagnosticsBundle.redactEntry scrubs ssid/gateway_ip/
    # portal_domain off AuditEntry objects, and `covering` mode's audit log is
    # ALWAYS empty by design (no session ever opens there — see
    # check_audit_absent) — not merely an edge case this run happened to hit.
    # Gating on absent `identifiers` alone would make this check fail on
    # EVERY real covering-mode run, which is a false positive, not a #134/#135
    # catch: there is no PII in the picture there at all to have leaked.
    if mode == "excluding":
        identifiers = {
            str(e[k])
            for e in audit_entries
            for k in ("ssid", "gateway_ip", "portal_domain")
            if e.get(k)
        }
        if not identifiers:
            fail(
                "bundle.redacted",
                "no ssid/gateway_ip/portal_domain in the audit log to test redaction "
                "against — cannot conclude the bundle is scrubbed",
                failures,
            )
        else:
            leaked = sorted(v for v in identifiers if v in bundle)
            if leaked:
                fail("bundle.redacted", f"redacted bundle still contains {leaked}", failures)
            elif "REDACTED" not in bundle:
                fail("bundle.redacted", "no REDACTED token — was redaction applied at all?", failures)
            else:
                ok("bundle.redacted", f"{len(identifiers)} identifier(s) scrubbed")

    # Evidence-cleared / confinement schema check — mode-specific, per the
    # docstring above. `excluding` validates and the tracker's clearIf() for
    # that network clears the evidence before this bundle is pulled, so the bundle must show the
    # cleared prose; a `confinement: ` line here is the evidence-block analog
    # of bundle.capture_cleared below — it means the evidence outlived the
    # incident it describes. `covering` never validates anything, so nothing
    # ever clears `_evidence`, and the classified state must still be there.
    if mode == "excluding":
        if "(no incident evidence captured)" in bundle:
            ok("bundle.evidence_cleared", "evidence cleared on the validated transition")
        else:
            fail(
                "bundle.evidence_cleared",
                "expected '(no incident evidence captured)' after validation; the "
                "retained incident evidence outlived the incident it describes",
                failures,
            )
    else:
        if "confinement: tunnelled" in bundle:
            ok("bundle.confinement", "confinement field present (tunnelled)")
        elif "(no incident evidence captured)" in bundle:
            fail(
                "bundle.confinement",
                "bundle shows '(no incident evidence captured)' but covering mode "
                "never validates anything — the evidence must still be there",
                failures,
            )
        else:
            fail(
                "bundle.confinement",
                "bundle missing a 'confinement: tunnelled' field",
                failures,
            )

    # The capture must not outlive its incident. Only meaningful in `excluding`
    # mode: this bundle is taken after wait_validated, which clears the
    # retained capture, so a populated capture block here means a previous
    # gateway's evidence is riding along in a report about this one. `covering`
    # mode never validates anything — there is no incident to have outlived.
    if mode == "excluding":
        if "(no intercepted response captured)" in bundle:
            ok("bundle.capture_cleared", "capture cleared on the validated transition")
        else:
            fail(
                "bundle.capture_cleared",
                "expected '(no intercepted response captured)' after validation; the "
                "retained capture outlived the incident it describes",
                failures,
            )

    # Body-derived fields were removed because a gateway controls them. Guard
    # the removal end-to-end, not just in the unit test.
    resurrected = [f for f in ("body_sha256", "body_characters") if f in bundle]
    if resurrected:
        fail("bundle.no_body_evidence", f"gateway-controlled field(s) back: {resurrected}", failures)
    else:
        ok("bundle.no_body_evidence", "no body-derived fields")


def _summarise(data: dict[str, Any]) -> str:
    parts = []
    for k, v in data.items():
        if isinstance(v, (int, str, bool, float)) and len(str(v)) <= 64:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Host-side assertions for the Gatepath Android e2e harness."
    )
    p.add_argument("artifacts_dir", help="directory containing the pulled artefacts")
    p.add_argument(
        "--vpn-mode",
        choices=("covering", "excluding"),
        default="excluding",
        help=(
            "covering: a third-party VPN covers Gatepath — expect TUNNELLED, "
            "no session, a silent sink. excluding: Gatepath is excluded from "
            "the VPN, the shipped contract — expect CONFINED, sign-in "
            "completes end-to-end. Default matches the pre-Task-14 CI shape."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv[1:])
    mode = args.vpn_mode
    root = Path(args.artifacts_dir)
    failures: list[str] = []
    # Read once, up front: several sections below need logcat, the audit
    # entries and the gateway entries regardless of which mode branch uses
    # them.
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
    expected_steps = run_scenario.step_names(
        run_scenario.STEPS_COVERING if mode == "covering" else run_scenario.STEPS_EXCLUDING
    )
    check_scenario(report, expected_steps, mode, failures)

    # G. The state MainViewModel actually classified — required in both modes.
    state_path = root / "confinement-state.txt"
    state_text = (
        state_path.read_text(errors="replace") if state_path.exists() else ""
    )
    check_confinement(state_text, mode, failures)

    audit_path = root / "audit_log.jsonl"
    audit_file_present = audit_path.exists() and audit_path.stat().st_size > 0
    if audit_file_present:
        audit_entries = [
            json.loads(line)
            for line in audit_path.read_text().splitlines()
            if line.strip()
        ]

    gateway_path = root / "gateway-log.json"
    if not gateway_path.exists():
        failures.append("gateway.file: gateway-log.json missing")
        print(f"  ✗ gateway-log.json missing in {root}", file=sys.stderr)
    else:
        gateway_entries = json.loads(gateway_path.read_text())

    sink_path = root / "vpn-sink.jsonl"

    # Per-mode routing. Each branch lists its own check letters end to end —
    # see the module docstring for the same list in prose. D is NOT shared
    # between the branches: `covering` is the ONLY mode where the sink is an
    # oracle at all. STEPS_EXCLUDING never lays a bound_begin/bound_end
    # marker pair — Gatepath itself is EXCLUDED from the VPN in that mode, so
    # its own traffic never rides the tunnel the sink watches (see
    # step_liveness_probe/step_settle_covering in run-scenario.py, both
    # `covering`-only steps) — so running check_vpn_confinement there would
    # hard-fail on `vpn.markers` on every correct excluding-mode run, not
    # just a broken one.
    if mode == "covering":
        # B. A Tunnelled session must never complete. An empty/missing audit
        # log is exactly what this mode should produce — a PASS here, unlike
        # the "session never completed?" failure excluding mode raises on the
        # same input (see check_audit_absent vs. check_app_audit).
        check_audit_absent(audit_entries, failures)
        # C'. No WebView ever opens while Tunnelled, so the mock must never
        # see a /portal hit from an Android UA.
        check_gateway_silent(gateway_entries, failures)
        # D. The sink IS the oracle here, and is required: the bound window
        # must be silent because the covered probe's connect() fails with
        # EPERM before a packet leaves the device.
        sink_present = sink_path.exists() and sink_path.stat().st_size > 0
        if not sink_present:
            failures.append("vpn.file: vpn-sink.jsonl missing or empty")
            print(f"  ✗ vpn-sink.jsonl missing or empty in {root}", file=sys.stderr)
        else:
            sink_lines = [
                json.loads(line)
                for line in sink_path.read_text().splitlines()
                if line.strip()
            ]
            check_vpn_silent_while_tunnelled(sink_lines, failures)
    else:
        # B / B'. At least one completed session, and it must be confined.
        if not audit_file_present:
            failures.append("audit.file: audit_log.jsonl missing or empty")
            print(f"  ✗ audit_log.jsonl missing or empty in {root}", file=sys.stderr)
        else:
            check_app_audit(audit_entries, failures)
            check_audit_confined(audit_entries, failures)

        # C. The portal must actually have been requested by an Android UA.
        check_gateway_log(gateway_entries, report, failures)

        # E. Off-domain traffic — needs the gateway log, the audit log AND
        # logcat, so it runs after all three have been read. Missing
        # artifacts leave their lists empty, which check_off_domain correctly
        # treats as "no evidence" rather than as a pass.
        check_off_domain(gateway_entries, audit_entries, logcat_text, failures)

        # D does NOT run in `excluding` mode — see the routing comment above
        # this if/else. The sink is still pulled (COMMON_TAIL runs for both
        # modes), purely for the record; note that and move on rather than
        # asserting anything about its contents.
        if sink_path.exists():
            ok("vpn.sink", "pulled for the record; not an oracle in excluding mode")

    # F. The shared bundle — needs the pulled URI sidecar and the audit
    # entries (for the identifiers redaction is checked against). Runs in
    # both modes; check_diagnostics_bundle itself gates the capture-cleared
    # check on `mode`.
    bundle_path = root / "diagnostics-bundle.txt"
    bundle_text = (
        bundle_path.read_text(errors="replace") if bundle_path.exists() else ""
    )
    uri_path = root / "bundle-uri.txt"
    uri_text = uri_path.read_text(errors="replace") if uri_path.exists() else ""
    check_diagnostics_bundle(bundle_text, audit_entries, uri_text, mode, failures)

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for f in failures:
            print(f"  • {f}", file=sys.stderr)
        return 1

    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
