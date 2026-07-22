"""Tests for the desktop diagnostic engine."""
from __future__ import annotations

import time

from gatepath.diag.engine import _RANK, DiagnosticEngine
from gatepath.diag.probe import HttpFetchResult, ProbeContext
from gatepath.diag.report import (
    ActionId,
    Cause,
    Healthy,
    HttpProxyBlocking,
    NoDnsServers,
    PrivateDnsBlocking,
    VpnBlocking,
)

NOOP_CTX = ProbeContext(
    interface_name="wlan0",
    probe_url="http://portal.test/probe",
    vpn_interfaces=(),
    http_proxy_description=None,
    dns_server_count=1,
    http_fetch=lambda url, accept: HttpFetchResult(None, None, None, None, "not wired"),
    resolve_host=lambda host: (),
    now_epoch_seconds=lambda: 0.0,
    active_probe=lambda: None,
)


class StubProbe:
    def __init__(self, name: str, report: object, delay: float = 0.0) -> None:
        self.name = name
        self._report = report
        self._delay = delay

    def run(self, ctx: ProbeContext) -> object:
        if self._delay:
            time.sleep(self._delay)
        return self._report


class ExplodingProbe:
    name = "boom"

    def run(self, ctx: ProbeContext) -> object:
        raise RuntimeError("probe exploded")


def test_all_healthy_yields_healthy_and_no_action() -> None:
    engine = DiagnosticEngine([StubProbe("a", Healthy()), StubProbe("b", Healthy())])
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.HEALTHY
    assert result.recommended.action_id is None


def test_top_finding_is_the_highest_ranked_report() -> None:
    engine = DiagnosticEngine(
        [
            StubProbe("proxy", HttpProxyBlocking(description="p:3128")),
            StubProbe("vpn", VpnBlocking(interface_name="tun0", is_full_tunnel=True)),
            StubProbe("ok", Healthy()),
        ]
    )
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.VPN_BLOCKING
    assert result.recommended.action_id == ActionId.PAUSE_VPN


def test_no_dns_outranks_http_proxy() -> None:
    engine = DiagnosticEngine(
        [
            StubProbe("proxy", HttpProxyBlocking(description="p:3128")),
            StubProbe("nodns", NoDnsServers()),
        ]
    )
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.NO_DNS_SERVERS
    assert result.recommended.action_id == ActionId.RECONNECT_NETWORK


def test_private_dns_blocking_yields_disable_private_dns_action() -> None:
    engine = DiagnosticEngine(
        [StubProbe("private_dns", PrivateDnsBlocking(resolver_host="1.1.1.1"))]
    )
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.PRIVATE_DNS_BLOCKING
    assert result.recommended.action_id == ActionId.DISABLE_PRIVATE_DNS
    assert "DNS-over-TLS" in result.recommended.instruction


def test_private_dns_blocking_rank_sits_between_no_dns_and_http_proxy() -> None:
    assert _RANK[Cause.PRIVATE_DNS_BLOCKING] == 80
    assert (
        _RANK[Cause.HTTP_PROXY_BLOCKING]
        < _RANK[Cause.PRIVATE_DNS_BLOCKING]
        < _RANK[Cause.NO_DNS_SERVERS]
    )


def test_no_dns_outranks_private_dns_blocking() -> None:
    engine = DiagnosticEngine(
        [
            StubProbe("private_dns", PrivateDnsBlocking(resolver_host="1.1.1.1")),
            StubProbe("nodns", NoDnsServers()),
        ]
    )
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.NO_DNS_SERVERS


def test_checks_carry_probe_names_in_probe_list_order() -> None:
    # The first probe in the list is deliberately the slower one, so
    # completion order (ok finishes first) provably diverges from list
    # order (vpn, ok). An implementation that built `checks` from
    # completion order rather than list order would fail this.
    engine = DiagnosticEngine(
        [
            StubProbe("vpn", VpnBlocking(interface_name="tun0", is_full_tunnel=True), delay=0.3),
            StubProbe("ok", Healthy()),
        ]
    )
    result = engine.run(NOOP_CTX)
    assert [c.probe_name for c in result.checks] == ["vpn", "ok"]


def test_a_raising_probe_becomes_inconclusive_without_killing_the_run() -> None:
    engine = DiagnosticEngine([ExplodingProbe(), StubProbe("ok", Healthy())])
    result = engine.run(NOOP_CTX)
    boom = next(c for c in result.checks if c.probe_name == "boom")
    assert boom.report.cause is Cause.INCONCLUSIVE
    assert "probe exploded" in boom.report.probe_errors[0]


def test_a_probe_over_its_budget_becomes_inconclusive() -> None:
    engine = DiagnosticEngine(
        [StubProbe("slow", Healthy(), delay=0.5), StubProbe("fast", Healthy())],
        total_budget_seconds=1.0,
        per_probe_budget_seconds=0.1,
    )
    result = engine.run(NOOP_CTX)
    slow = next(c for c in result.checks if c.probe_name == "slow")
    assert slow.report.cause is Cause.INCONCLUSIVE
    fast = next(c for c in result.checks if c.probe_name == "fast")
    assert fast.report.cause is Cause.HEALTHY


def test_inconclusive_ranks_below_every_real_finding() -> None:
    engine = DiagnosticEngine([ExplodingProbe(), StubProbe("proxy", HttpProxyBlocking(description="p:3128"))])
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.HTTP_PROXY_BLOCKING


def test_run_returns_well_before_a_hung_probe_finishes() -> None:
    # Proves the budget is a real wall-clock ceiling on run(), not just on
    # the reported result: a probe sleeping far past the total budget must
    # not make run() block for anywhere near the sleep duration. Against the
    # old `with ThreadPoolExecutor(...) as pool:` form, shutdown(wait=True)
    # in __exit__ would block until the sleep finished, so this test fails
    # (times out around 3s) on the pre-fix code.
    engine = DiagnosticEngine(
        [StubProbe("hung", Healthy(), delay=3.0)],
        total_budget_seconds=0.3,
        per_probe_budget_seconds=0.3,
    )
    start = time.monotonic()
    result = engine.run(NOOP_CTX)
    elapsed = time.monotonic() - start
    assert elapsed < 1.5, f"run() took {elapsed:.2f}s; budget should have bounded it"
    hung = next(c for c in result.checks if c.probe_name == "hung")
    assert hung.report.cause is Cause.INCONCLUSIVE
