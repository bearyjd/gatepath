"""Unit tests for the VPN-sink no-leak assertion.

NOTE: this shares a basename with tests/e2e-docker/driver/test_assertions.py,
and neither dir has an __init__.py. Run them in SEPARATE pytest invocations
(see .github/workflows/e2e-driver-tests.yml) — one invocation over both errors
out with "import file mismatch" before running anything.
"""
from __future__ import annotations

import assertions

BEGIN = {"marker": "bound_begin", "t": 2.0}
END = {"marker": "bound_end", "t": 9.0}
# The unbound liveness probe and the bound WebView's <img> both target the
# dedicated sentinel host:port (10.0.2.2:18081).
SENTINEL = {"dst": "10.0.2.2", "port": 18081, "proto": "TCP", "t": 1.0}
SENTINEL_LEAK = {"dst": "10.0.2.2", "port": 18081, "proto": "TCP", "t": 5.0}
# Captive-monitor traffic to the mock's own port (:18080) — expected unbound
# noise inside the bound window that must NOT be flagged as a leak.
CAPTIVE_MONITOR = {"dst": "10.0.2.2", "port": 18080, "proto": "TCP", "t": 5.0}


def test_confined_passes():
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL, BEGIN, END], failures, sentinel_attempted=True)
    assert failures == []


def test_leak_fails_and_names_dst():
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL, BEGIN, SENTINEL_LEAK, END], failures, sentinel_attempted=True)
    assert any("LEAK" in f and "10.0.2.2" in f and "18081" in f for f in failures)


def test_captive_monitor_noise_ignored():
    # A 10.0.2.2:18080 (captive-monitor) packet inside the bound window is not a
    # leak — only the dedicated sentinel port counts toward D2. This is the D2
    # disambiguation the port-based sentinel exists to provide.
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL, BEGIN, CAPTIVE_MONITOR, END], failures, sentinel_attempted=True)
    assert failures == []


def test_confined_but_not_attempted_is_inconclusive():
    # A clean bound window must NOT pass when the WebView never attempted the
    # sentinel — D2's positive control against a vacuous pass.
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL, BEGIN, END], failures, sentinel_attempted=False)
    assert any("inconclusive" in f for f in failures)


def test_missing_liveness_is_vacuous_fail():
    failures: list[str] = []
    assertions.check_vpn_confinement([BEGIN, END], failures, sentinel_attempted=True)
    assert any("liveness" in f for f in failures)


def test_missing_markers_fails():
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL], failures, sentinel_attempted=True)
    assert any("marker" in f for f in failures)


def test_reversed_markers_fail():
    # bound_end appearing before bound_begin must hard-fail, never pass — the
    # spec calls out out-of-order markers as a hard fail.
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL, END, BEGIN], failures, sentinel_attempted=True)
    assert any("marker" in f for f in failures)


# ── Off-domain traffic (section E) ────────────────────────────────────────
#
# These mirror the D2 positive-control discipline above. The assertion this
# section replaces could not fail: it passed whenever no off-domain host
# appeared in the gateway log, which is exactly what a WebView that never
# attempted any off-domain request produces. Artifacts from the last green
# run confirmed all three signals were absent at once.

GW_PORTAL = {"path": "/portal", "headers": {"Host": "10.0.2.2:18080",
                                            "User-Agent": "Mozilla/5.0 (Linux; Android 14)"}}
GW_OFF_DOMAIN = {"path": "/track.js", "headers": {"Host": "evil-tracker.example.com"}}
AUDIT_COUNTED = {"blocked_navigation_attempts": 1, "blocked_resource_requests": 0}
AUDIT_ZERO = {"blocked_navigation_attempts": 0, "blocked_resource_requests": 0}
# Verbatim from CI run 30418445289. The request was ALLOWED out and DNS is what
# stopped it — neither off-domain hostname resolves in the emulator. A refusal
# short-circuits in shouldOverrideUrlLoading / shouldInterceptRequest and never
# reaches DNS, so a net:: transport error is positive proof of allow.
LOGCAT_ALLOWED_DNS_FAILED = (
    "07-28 17:39:38.317  3669  3669 W GatepathWebView: onReceivedError "
    "https://evil-tracker.example.com/track.js: code=-2 "
    "desc=net::ERR_NAME_NOT_RESOLVED isMainFrame=false\n"
)
# The off-domain host actually loaded — the other allow shape.
LOGCAT_ALLOWED_LOADED = (
    "07-28 17:39:38.317  3669  3669 D GatepathWebView: Page started: "
    "https://external-site.example.com/grant\n"
)
# A refusal: the WebView names the host, but the request produced no
# network-stack outcome at all. Returning true from shouldOverrideUrlLoading
# fires no onReceivedError, which is exactly why "no trace" is the tell.
LOGCAT_REFUSED = (
    "07-28 17:39:38.317  3669  3669 D GatepathWebView: Off-domain main-frame "
    "navigation to https://external-site.example.com/grant "
    "(portal host=10.0.2.2) — blocking\n"
)
# The other refusal shape: an app-side error code. ERR_BLOCKED_BY_CLIENT is
# raised by the app short-circuiting the request, not by the network stack.
LOGCAT_REFUSED_BY_CLIENT = (
    "07-28 17:39:38.317  3669  3669 W GatepathWebView: onReceivedError "
    "https://evil-tracker.example.com/track.js: code=-20 "
    "desc=net::ERR_BLOCKED_BY_CLIENT isMainFrame=false\n"
)


def test_off_domain_no_evidence_at_all_is_a_failure():
    """The regression this section exists for.

    No gateway hit, no counter, no WebView log line — the claim is simply
    unproven, and the old assertion reported it as a pass.
    """
    failures: list[str] = []
    assertions.check_off_domain([GW_PORTAL], [AUDIT_ZERO], "", failures)
    assert any("not_exercised" in f for f in failures), failures


def test_off_domain_gateway_evidence_passes():
    """Reached the captive gateway = allowed and confined, which is the design."""
    failures: list[str] = []
    assertions.check_off_domain(
        [GW_PORTAL, GW_OFF_DOMAIN], [AUDIT_COUNTED], LOGCAT_ALLOWED_DNS_FAILED, failures
    )
    assert failures == [], failures


def test_off_domain_counted_without_gateway_hit_still_counts_as_evidence():
    """DNS for the off-domain host need not resolve.

    onBlockedNavigation() fires in shouldOverrideUrlLoading BEFORE the request
    goes out, so a non-zero counter is real evidence even when nothing reaches
    the gateway.
    """
    failures: list[str] = []
    assertions.check_off_domain([GW_PORTAL], [AUDIT_COUNTED], "", failures)
    assert failures == [], failures


def test_off_domain_dns_failure_alone_is_an_allow_not_a_refusal():
    """The shape every real CI run produces, and it must PASS.

    Neither off-domain hostname resolves in the emulator and neither is in
    BlockedDomains, so a correctly-allowed request reaches no gateway and fires
    no counter. Its only trace is the WebView's own transport error — which is
    proof the app let the request out, since a refusal never reaches DNS.
    Keying the #119 guard off "attempted and not counted" failed on exactly
    this (CI run 30418445289).
    """
    failures: list[str] = []
    assertions.check_off_domain(
        [GW_PORTAL], [AUDIT_ZERO], LOGCAT_ALLOWED_DNS_FAILED, failures
    )
    assert failures == [], failures


def test_off_domain_page_load_alone_is_an_allow():
    failures: list[str] = []
    assertions.check_off_domain([GW_PORTAL], [AUDIT_ZERO], LOGCAT_ALLOWED_LOADED, failures)
    assert failures == [], failures


def test_off_domain_attempted_but_refused_fails():
    """The #119 regression guard.

    The WebView named an off-domain host, and the request left no trace at all
    — no network-stack outcome, no counter, no gateway hit. That is the shape
    of going back to refusing, which breaks cross-host sign-in on Meraki /
    Cisco ISE / UniFi.
    """
    failures: list[str] = []
    assertions.check_off_domain([GW_PORTAL], [AUDIT_ZERO], LOGCAT_REFUSED, failures)
    assert any("refus" in f.lower() for f in failures), failures


def test_off_domain_blocked_by_client_is_a_refusal():
    """ERR_BLOCKED_BY_CLIENT is raised by the app, not the network stack.

    It must not be mistaken for evidence that the request went out — otherwise
    the most literal form of the #119 regression would read as a pass.
    """
    failures: list[str] = []
    assertions.check_off_domain(
        [GW_PORTAL], [AUDIT_ZERO], LOGCAT_REFUSED_BY_CLIENT, failures
    )
    assert any("refus" in f.lower() for f in failures), failures


def test_off_domain_ignores_non_webview_logcat_lines():
    """Only the WebView's OWN log counts as an attempt.

    Any other component naming the host (the harness echoing config, say) is
    not evidence that the WebView tried to load it — the same rule
    _webview_attempted_sentinel applies for D2.
    """
    noise = "07-28 17:39:38.317  1  1 I SomeOtherTag: evil-tracker.example.com\n"
    failures: list[str] = []
    assertions.check_off_domain([GW_PORTAL], [AUDIT_ZERO], noise, failures)
    assert any("not_exercised" in f for f in failures), failures
