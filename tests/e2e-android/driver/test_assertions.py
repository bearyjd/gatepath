"""Unit tests for the VPN-sink no-leak assertion."""
from __future__ import annotations

import assertions

BEGIN = {"marker": "bound_begin", "t": 2.0}
END = {"marker": "bound_end", "t": 9.0}
SENTINEL = {"dst": "203.0.113.7", "port": 9, "proto": "UDP", "t": 1.0}
PORTAL_LEAK = {"dst": "10.0.2.2", "port": 18080, "proto": "TCP", "t": 5.0}


def test_confined_passes():
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL, BEGIN, END], failures)
    assert failures == []


def test_leak_fails_and_names_dst():
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL, BEGIN, PORTAL_LEAK, END], failures)
    assert any("LEAK" in f and "10.0.2.2" in f for f in failures)


def test_missing_liveness_is_vacuous_fail():
    failures: list[str] = []
    assertions.check_vpn_confinement([BEGIN, END], failures)
    assert any("liveness" in f for f in failures)


def test_missing_markers_fails():
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL], failures)
    assert any("marker" in f for f in failures)
