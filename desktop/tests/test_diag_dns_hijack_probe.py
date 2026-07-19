"""Tests for the DNS-hijack probe.

Mirror of `android/app/src/test/java/com/ventouxlabs/gatepath/diag/DnsHijackProbeTest.kt`.
"""
from __future__ import annotations

from gatepath.diag.dns_hijack_probe import DnsHijackProbe
from gatepath.diag.probe import HttpFetchResult, ProbeContext
from gatepath.diag.report import Cause
from gatepath.portal_probe import ProbeResult


def _doh_body(*addresses: str) -> str:
    answers = ",".join(
        f'{{"name":"connectivitycheck.gstatic.com","type":1,"data":"{addr}"}}' for addr in addresses
    )
    return f'{{"Status":0,"Answer":[{answers}]}}'


def ctx(**overrides: object) -> ProbeContext:
    defaults: dict[str, object] = {
        "interface_name": "wlan0",
        "probe_url": "http://connectivitycheck.gstatic.com/generate_204",
        "vpn_interfaces": (),
        "http_proxy_description": None,
        "dns_server_count": 1,
        "http_fetch": lambda url, accept: HttpFetchResult(None, None, None, None, "unused"),
        "resolve_host": lambda host: (),
        "now_epoch_seconds": lambda: 0.0,
        "active_probe": lambda: ProbeResult(status="validated"),
    }
    defaults.update(overrides)
    return ProbeContext(**defaults)  # type: ignore[arg-type]


def _doh_fetch(doh_result: HttpFetchResult):
    def _fetch(url: str, accept: object) -> HttpFetchResult:
        if accept == "application/dns-json":
            return doh_result
        return HttpFetchResult(None, None, None, None, f"wrong accept: {accept}")

    return _fetch


def test_private_system_answer_with_public_doh_answer_is_a_hijack() -> None:
    report = DnsHijackProbe().run(
        ctx(
            resolve_host=lambda host: ("192.168.1.1",),
            http_fetch=_doh_fetch(HttpFetchResult(200, None, None, _doh_body("142.250.180.14"), None)),
        )
    )
    assert report.cause is Cause.DNS_HIJACK
    assert report.host_probed == "connectivitycheck.gstatic.com"
    assert report.system_answer == "192.168.1.1"
    assert report.doh_answer == "142.250.180.14"


def test_matching_public_answers_are_healthy() -> None:
    report = DnsHijackProbe().run(
        ctx(
            resolve_host=lambda host: ("142.250.180.14",),
            http_fetch=_doh_fetch(HttpFetchResult(200, None, None, _doh_body("142.250.180.14"), None)),
        )
    )
    assert report.cause is Cause.HEALTHY


def test_system_resolution_failure_is_inconclusive() -> None:
    report = DnsHijackProbe().run(
        ctx(
            resolve_host=lambda host: (),
            http_fetch=_doh_fetch(HttpFetchResult(200, None, None, _doh_body("1.2.3.4"), None)),
        )
    )
    assert report.cause is Cause.INCONCLUSIVE


def test_doh_unreachable_is_healthy_expected_while_captive() -> None:
    report = DnsHijackProbe().run(
        ctx(
            resolve_host=lambda host: ("10.0.0.1",),
            http_fetch=_doh_fetch(HttpFetchResult(None, None, None, None, "timeout")),
        )
    )
    assert report.cause is Cause.HEALTHY


def test_malformed_doh_json_is_healthy_never_a_crash() -> None:
    report = DnsHijackProbe().run(
        ctx(
            resolve_host=lambda host: ("10.0.0.1",),
            http_fetch=_doh_fetch(HttpFetchResult(200, None, None, "not json {", None)),
        )
    )
    assert report.cause is Cause.HEALTHY


def test_public_system_answer_is_healthy_even_if_doh_differs() -> None:
    report = DnsHijackProbe().run(
        ctx(
            resolve_host=lambda host: ("8.8.8.8",),
            http_fetch=_doh_fetch(HttpFetchResult(200, None, None, _doh_body("142.250.180.14"), None)),
        )
    )
    assert report.cause is Cause.HEALTHY


def test_unparseable_probe_url_is_inconclusive() -> None:
    report = DnsHijackProbe().run(ctx(probe_url="not a url"))
    assert report.cause is Cause.INCONCLUSIVE


def test_declines_without_probing_when_default_route_bypasses_captive() -> None:
    called = {"resolve_host": False, "http_fetch": False}

    def resolve_host(host: str):
        called["resolve_host"] = True
        return ()

    def http_fetch(url: str, accept: object) -> HttpFetchResult:
        called["http_fetch"] = True
        return HttpFetchResult(200, None, None, _doh_body("1.2.3.4"), None)

    report = DnsHijackProbe().run(
        ctx(
            resolve_host=resolve_host,
            http_fetch=http_fetch,
            default_route_bypasses_captive=True,
        )
    )
    assert report.cause is Cause.INCONCLUSIVE
    assert "default route is not the captive network" in report.probe_errors[0]
    assert called["resolve_host"] is False
    assert called["http_fetch"] is False
