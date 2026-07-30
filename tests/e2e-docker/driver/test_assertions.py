"""Unit tests for the desktop E2E gateway assertions.

These exist because of issue #120: the off-domain claim in this harness was
described as "the most-load-bearing security claim Gatepath makes" while being,
in practice, decorative. #121 fixed the *content* of the claim (prevention →
observe-and-count-and-confine). What it left behind were the escape hatches
that let the claim report ✓ without ever having been evaluated.

The rule these tests encode is the one CLAUDE.md states for this repo: a step
can succeed without the invariant it was meant to prove actually holding, so
"nothing happened" must never render as a pass.

NOTE: this shares a basename with tests/e2e-android/driver/test_assertions.py,
and neither dir has an __init__.py. Run them in SEPARATE pytest invocations
(see .github/workflows/e2e-driver-tests.yml) — one invocation over both errors
out with "import file mismatch" before running anything.
"""
from __future__ import annotations

import json

import assertions

GW_PORTAL = {"path": "/portal", "headers": {"Host": "10.99.0.1"}}
GW_OFF_DOMAIN_TRACKER = {
    "path": "/track.js",
    "headers": {"Host": "evil-tracker.example.com"},
}
GW_OFF_DOMAIN_SITE = {
    "path": "/grant",
    "headers": {"Host": "external-site.example.com"},
}


def _report(*, subprocess_alive: bool) -> dict:
    return {
        "steps": [
            {
                "name": "dwell_and_screenshot",
                "ok": True,
                "data": {"subprocess_alive": subprocess_alive},
            }
        ]
    }


LIVE = _report(subprocess_alive=True)
DEAD = _report(subprocess_alive=False)


# ── The happy path ────────────────────────────────────────────────────────


def test_off_domain_served_by_gateway_passes():
    """The design's actual claim: allowed, counted, and confined to the netns.

    dnsmasq hijacks every A query to the gateway IP, so an off-domain request
    the app correctly allows *does* land in this log. Appearing here is the
    evidence of confinement — unlike the Android emulator, where the same
    hostnames do not resolve at all.
    """
    failures: list[str] = []
    assertions.check_gateway_log(
        [GW_PORTAL, GW_OFF_DOMAIN_TRACKER, GW_OFF_DOMAIN_SITE], LIVE, failures
    )
    assert failures == [], failures


def test_portal_loaded_but_no_off_domain_fails():
    """The regression guard: going back to refusing off-domain traffic.

    The portal page references both off-domain hosts, so a live WebView that
    produced no off-domain contact at all is the shape of a refusal — which
    breaks cross-host sign-in on Meraki / Cisco ISE / UniFi.
    """
    failures: list[str] = []
    assertions.check_gateway_log([GW_PORTAL], LIVE, failures)
    assert any("off_domain" in f for f in failures), failures


def test_live_webview_that_never_hit_portal_fails():
    failures: list[str] = []
    assertions.check_gateway_log([GW_OFF_DOMAIN_TRACKER], LIVE, failures)
    assert any("portal_hit" in f for f in failures), failures


def test_empty_gateway_log_fails():
    failures: list[str] = []
    assertions.check_gateway_log([], LIVE, failures)
    assert any("gateway.entries" in f for f in failures), failures


# ── Issue #120: "nothing happened" must not render as a pass ──────────────


def test_dead_webview_does_not_silently_pass_portal_hit():
    """A dead WebView subprocess used to auto-✓ this check.

    The excuse in the code was the stripped container env (no session bus, no
    GNOME services) killing WebKit's renderer. But check_gateway_log is only
    reached on the NON-skipped path — the privileged, real-PHY substrate whose
    entire purpose is to run the portal. On that path a dead WebView means the
    harness did not do its job, and #118 (WebView could not be constructed on
    WebKit 6.0 *at all*) is exactly the bug that survived by hiding here.
    """
    failures: list[str] = []
    assertions.check_gateway_log([GW_PORTAL], DEAD, failures)
    assert any("portal_hit" in f for f in failures), failures


def test_dead_webview_does_not_silently_pass_off_domain():
    """The headline claim must not report ✓ when it was never evaluated.

    This is the desktop twin of the Android section-E bug: the pass branch
    fired precisely when there was nothing to judge.
    """
    failures: list[str] = []
    assertions.check_gateway_log([GW_PORTAL], DEAD, failures)
    assert any("off_domain" in f for f in failures), failures


def test_missing_dwell_step_is_reported_as_harness_drift_not_a_webview_bug():
    """An absent signal is not a dead WebView.

    `.get(...).get(...)` returns None for a missing step, a renamed step, or a
    report that never wrote the key. Collapsing that into "the WebView died"
    sends the next engineer to debug WebKit 6.0 over what is actually scenario
    report drift.
    """
    failures: list[str] = []
    assertions.check_gateway_log([GW_PORTAL], {"steps": []}, failures)
    assert any("drift" in f for f in failures), failures
    assert not any("WebKit" in f for f in failures), failures


def test_dwell_step_present_but_missing_the_key_is_also_drift():
    failures: list[str] = []
    report = {"steps": [{"name": "dwell_and_screenshot", "ok": True, "data": {}}]}
    assertions.check_gateway_log([GW_PORTAL], report, failures)
    assert any("drift" in f for f in failures), failures


def test_veth_substrate_never_reaches_the_gateway_checks(tmp_path, capsys):
    """The precondition the dead-WebView hard-fail depends on.

    check_gateway_log fails on a dead WebView. That is only correct because the
    veth substrate returns at check_scenario_skipped and never calls it. If
    that routing ever changes, this hard-fail starts firing on a substrate that
    legitimately never runs the portal — so pin it here rather than trusting a
    comment.
    """
    steps = [
        {"name": n, "ok": True, "data": {}}
        for n in ("reset_gateway", "probe", "snapshot_gateway_log",
                  "sentinel_baseline", "nm_lookup", "helper_connect")
    ]
    steps.append({"name": "privileged_path", "ok": True, "data": {"skipped": True}})
    (tmp_path / "scenario-report.json").write_text(
        json.dumps({"rc": 0, "steps": steps})
    )
    rc = assertions.main(["assertions.py", str(tmp_path)])
    out = capsys.readouterr().out
    assert "gateway.portal_hit" not in out
    assert "gateway.off_domain_confined" not in out
    assert rc == 0


def test_dead_webview_failure_names_the_cause():
    """The failure has to be actionable, not just red.

    Whoever dispatches hwsim next needs to know this is a WebView-startup
    problem, not an off-domain-blocking regression.
    """
    failures: list[str] = []
    assertions.check_gateway_log([GW_PORTAL], DEAD, failures)
    assert any("WebView" in f for f in failures), failures
