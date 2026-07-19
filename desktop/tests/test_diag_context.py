"""Tests for gatepath.diag_context — the platform-reading assembler.

Everything here injects environ / resolv_conf_path / a fake active_probe so
no test touches the real system (real DNS, real proxy env, real sockets).
"""
from __future__ import annotations

from unittest import mock

from gatepath import diag_context
from gatepath.diag.probe import VpnDetail
from gatepath.portal_probe import ProbeResult
from gatepath.vpn_detector import VpnInterface


# ---------------------------------------------------------------------------
# _proxy_description
# ---------------------------------------------------------------------------


def test_proxy_description_reads_https_proxy() -> None:
    assert diag_context._proxy_description({"https_proxy": "proxy.corp:3128"}) == "proxy.corp:3128"


def test_proxy_description_reads_uppercase_https_proxy() -> None:
    assert diag_context._proxy_description({"HTTPS_PROXY": "proxy.corp:3128"}) == "proxy.corp:3128"


def test_proxy_description_reads_http_proxy() -> None:
    assert diag_context._proxy_description({"http_proxy": "proxy.corp:8080"}) == "proxy.corp:8080"


def test_proxy_description_reads_uppercase_http_proxy() -> None:
    assert diag_context._proxy_description({"HTTP_PROXY": "proxy.corp:8080"}) == "proxy.corp:8080"


def test_proxy_description_prefers_https_over_http() -> None:
    environ = {"https_proxy": "https-proxy:3128", "http_proxy": "http-proxy:8080"}
    assert diag_context._proxy_description(environ) == "https-proxy:3128"


def test_proxy_description_prefers_lowercase_over_uppercase() -> None:
    environ = {"https_proxy": "lower:3128", "HTTPS_PROXY": "upper:3128"}
    assert diag_context._proxy_description(environ) == "lower:3128"


def test_proxy_description_none_when_neither_set() -> None:
    assert diag_context._proxy_description({}) is None


# ---------------------------------------------------------------------------
# _count_dns_servers
# ---------------------------------------------------------------------------


def test_count_dns_servers_counts_nameserver_lines(tmp_path) -> None:
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")
    assert diag_context._count_dns_servers(str(resolv)) == 2


def test_count_dns_servers_ignores_comments(tmp_path) -> None:
    resolv = tmp_path / "resolv.conf"
    resolv.write_text(
        "# nameserver 9.9.9.9\n"
        "nameserver 1.1.1.1\n"
        "  # nameserver 4.4.4.4\n"
        "search example.com\n"
    )
    assert diag_context._count_dns_servers(str(resolv)) == 1


def test_count_dns_servers_zero_for_missing_file(tmp_path) -> None:
    missing = tmp_path / "does-not-exist.conf"
    assert diag_context._count_dns_servers(str(missing)) == 0


def test_count_dns_servers_zero_for_unreadable_file(tmp_path) -> None:
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 1.1.1.1\n")
    resolv.chmod(0o000)
    try:
        assert diag_context._count_dns_servers(str(resolv)) == 0
    finally:
        resolv.chmod(0o644)  # restore so tmp_path cleanup can remove it


# ---------------------------------------------------------------------------
# build_probe_context
# ---------------------------------------------------------------------------


def _fake_probe(result: ProbeResult):
    return lambda url, timeout=5: result


def test_build_probe_context_wires_vpn_details(tmp_path) -> None:
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 1.1.1.1\n")

    with mock.patch(
        "gatepath.vpn_detector.detect_vpn_details",
        return_value=[VpnInterface(name="tun0", mode="split_tunnel")],
    ), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ):
        ctx = diag_context.build_probe_context(
            "wlan0",
            environ={},
            resolv_conf_path=str(resolv),
        )

    assert ctx.vpn_interfaces == (VpnDetail(name="tun0", is_full_tunnel=False),)
    assert isinstance(ctx.vpn_interfaces[0], VpnDetail)


def test_build_probe_context_marks_full_tunnel() -> None:
    with mock.patch(
        "gatepath.vpn_detector.detect_vpn_details",
        return_value=[VpnInterface(name="tailscale0", mode="full_tunnel")],
    ), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    assert ctx.vpn_interfaces[0].is_full_tunnel is True


def test_build_probe_context_reads_proxy_and_dns(tmp_path) -> None:
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")

    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ):
        ctx = diag_context.build_probe_context(
            "wlan0",
            environ={"https_proxy": "proxy.corp:3128"},
            resolv_conf_path=str(resolv),
        )

    assert ctx.http_proxy_description == "proxy.corp:3128"
    assert ctx.dns_server_count == 2


def test_build_probe_context_resolve_host_wraps_getaddrinfo() -> None:
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    with mock.patch(
        "socket.getaddrinfo",
        return_value=[
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("93.184.216.34", 0)),  # duplicate, should collapse
        ],
    ):
        assert ctx.resolve_host("example.com") == ("93.184.216.34",)


def test_build_probe_context_resolve_host_returns_empty_on_oserror() -> None:
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    with mock.patch("socket.getaddrinfo", side_effect=OSError("no such host")):
        assert ctx.resolve_host("nope.invalid") == ()


def test_build_probe_context_http_fetch_wires_to_http_fetcher() -> None:
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    sentinel = object()
    with mock.patch("gatepath.http_fetcher.fetch", return_value=sentinel) as fetch:
        result = ctx.http_fetch("http://example.test/", "text/html")

    fetch.assert_called_once_with("http://example.test/", "text/html")
    assert result is sentinel


def test_build_probe_context_now_epoch_seconds_is_time_time() -> None:
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    with mock.patch("time.time", return_value=12345.0):
        assert ctx.now_epoch_seconds() == 12345.0


def test_build_probe_context_active_probe_calls_portal_probe() -> None:
    result = ProbeResult(status="validated")
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe", return_value=result
    ) as probe_fn:
        ctx = diag_context.build_probe_context("wlan0", probe_url="http://probe.test/", environ={})

    assert ctx.active_probe() is result
    # Called once during context assembly (for the bypass derivation) and
    # again here via the injected callable — both must hit the same probe_url.
    for call in probe_fn.call_args_list:
        assert call.args[0] == "http://probe.test/" or call.kwargs.get("url") == "http://probe.test/"


# ---------------------------------------------------------------------------
# default_route_bypasses_captive derivation
# ---------------------------------------------------------------------------


def test_default_route_bypasses_captive_true_when_probe_validates() -> None:
    """NetworkManager flagged this interface captive (why we're diagnosing at
    all), yet the connectivity probe independently comes back "validated" —
    meaning it reached the real internet without going through the captive
    gateway. That contradiction means the default route is not the captive
    network."""
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe", return_value=ProbeResult(status="validated")
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    assert ctx.default_route_bypasses_captive is True


def test_default_route_bypasses_captive_false_when_probe_sees_portal() -> None:
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    assert ctx.default_route_bypasses_captive is False


def test_default_route_bypasses_captive_false_on_probe_error() -> None:
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe", return_value=ProbeResult(status="error", message="timeout")
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    assert ctx.default_route_bypasses_captive is False


# ---------------------------------------------------------------------------
# default_engine
# ---------------------------------------------------------------------------


def test_default_engine_declares_exactly_the_expected_probes() -> None:
    engine = diag_context.default_engine()
    names = {probe.name for probe in engine._probes}  # type: ignore[attr-defined]
    assert names == {
        "vpn",
        "dns_hijack",
        "no_dns",
        "http_proxy",
        "redirect_loop",
        "clock_skew",
        "https_only",
    }
    assert len(engine._probes) == 7  # type: ignore[attr-defined]
