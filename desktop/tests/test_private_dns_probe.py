"""Tests for the context-only PrivateDnsProbe."""
from __future__ import annotations

from gatepath.diag.private_dns_probe import PrivateDnsProbe
from gatepath.diag.probe import HttpFetchResult, ProbeContext
from gatepath.diag.report import Cause


def ctx(**overrides: object) -> ProbeContext:
    defaults: dict[str, object] = {
        "interface_name": "wlan0",
        "probe_url": "http://portal.test/probe",
        "vpn_interfaces": (),
        "http_proxy_description": None,
        "dns_server_count": 1,
        "http_fetch": lambda url, accept: HttpFetchResult(None, None, None, None, "unused"),
        "resolve_host": lambda host: (),
        "now_epoch_seconds": lambda: 0.0,
        "active_probe": lambda: None,
    }
    defaults.update(overrides)
    return ProbeContext(**defaults)  # type: ignore[arg-type]


def test_private_dns_probe_reports_active_with_resolver_host() -> None:
    report = PrivateDnsProbe().run(
        ctx(private_dns_active=True, private_dns_server="1.1.1.1")
    )
    assert report.cause is Cause.PRIVATE_DNS_BLOCKING
    assert report.resolver_host == "1.1.1.1"


def test_private_dns_probe_reports_active_without_resolver_host() -> None:
    report = PrivateDnsProbe().run(
        ctx(private_dns_active=True, private_dns_server=None)
    )
    assert report.cause is Cause.PRIVATE_DNS_BLOCKING
    assert report.resolver_host is None


def test_private_dns_probe_healthy_when_inactive() -> None:
    assert PrivateDnsProbe().run(ctx(private_dns_active=False)).cause is Cause.HEALTHY
