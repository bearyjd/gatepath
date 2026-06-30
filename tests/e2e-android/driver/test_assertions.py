"""Unit tests for the VPN-sink no-leak assertion."""
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
