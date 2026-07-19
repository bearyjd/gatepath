"""Tests for the desktop diagnostic cause vocabulary."""
from __future__ import annotations

import dataclasses

import pytest

from gatepath.diag.report import (
    Cause,
    ClockSkew,
    DnsHijack,
    Healthy,
    HttpProxyBlocking,
    HttpsOnlyCaptive,
    Inconclusive,
    NoDnsServers,
    PortalRedirectLoop,
    VpnBlocking,
)

ALL_REPORTS = [
    Healthy(),
    VpnBlocking(interface_name="tun0", is_full_tunnel=True),
    DnsHijack(host_probed="example.test", system_answer="192.168.1.1", doh_answer="93.184.216.34"),
    HttpProxyBlocking(description="proxy.corp:3128"),
    HttpsOnlyCaptive(https_error_message="connection reset"),
    NoDnsServers(),
    PortalRedirectLoop(chain=("http://a", "http://b", "http://a")),
    ClockSkew(skew_seconds=900),
    Inconclusive(probe_errors=("vpn: boom",)),
]


def test_every_report_exposes_a_cause() -> None:
    for report in ALL_REPORTS:
        assert isinstance(report.cause, Cause)


def test_cause_values_match_the_kotlin_variant_names() -> None:
    # PR 5's parity guard string-matches these against the Kotlin sealed
    # interface, so the spelling is a contract, not a label.
    assert {c.value for c in Cause} == {
        "Healthy",
        "VpnBlocking",
        "DnsHijack",
        "HttpProxyBlocking",
        "HttpsOnlyCaptive",
        "NoDnsServers",
        "PortalRedirectLoop",
        "ClockSkew",
        "Inconclusive",
    }


def test_every_cause_has_exactly_one_report_type() -> None:
    seen = [r.cause for r in ALL_REPORTS]
    assert sorted(c.value for c in seen) == sorted(c.value for c in Cause)


def test_reports_are_immutable() -> None:
    report = VpnBlocking(interface_name="tun0", is_full_tunnel=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.interface_name = "wg0"  # type: ignore[misc]
