"""Tests for the context-only desktop probes."""
from __future__ import annotations

from gatepath.diag.http_proxy_probe import HttpProxyProbe
from gatepath.diag.no_dns_probe import NoDnsProbe
from gatepath.diag.probe import HttpFetchResult, ProbeContext, VpnDetail
from gatepath.diag.report import Cause
from gatepath.diag.vpn_probe import VpnProbe


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


def test_vpn_probe_reports_the_first_interface() -> None:
    report = VpnProbe().run(
        ctx(vpn_interfaces=(VpnDetail("tun0", False), VpnDetail("wg0", False)))
    )
    assert report.cause is Cause.VPN_BLOCKING
    assert report.interface_name == "tun0"
    assert report.is_full_tunnel is False


def test_vpn_probe_propagates_full_tunnel() -> None:
    report = VpnProbe().run(ctx(vpn_interfaces=(VpnDetail("tailscale0", True),)))
    assert report.is_full_tunnel is True


def test_vpn_probe_healthy_without_a_vpn() -> None:
    assert VpnProbe().run(ctx()).cause is Cause.HEALTHY


def test_http_proxy_probe_reports_a_configured_proxy() -> None:
    report = HttpProxyProbe().run(ctx(http_proxy_description="proxy.corp:3128"))
    assert report.cause is Cause.HTTP_PROXY_BLOCKING
    assert report.description == "proxy.corp:3128"


def test_http_proxy_probe_healthy_without_a_proxy() -> None:
    assert HttpProxyProbe().run(ctx()).cause is Cause.HEALTHY


def test_no_dns_probe_reports_zero_servers() -> None:
    assert NoDnsProbe().run(ctx(dns_server_count=0)).cause is Cause.NO_DNS_SERVERS


def test_no_dns_probe_healthy_with_servers() -> None:
    assert NoDnsProbe().run(ctx(dns_server_count=2)).cause is Cause.HEALTHY
