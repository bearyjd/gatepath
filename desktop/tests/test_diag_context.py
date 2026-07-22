"""Tests for gatepath.diag_context — the platform-reading assembler.

Everything here injects environ / resolv_conf_path / a fake active_probe so
no test touches the real system (real DNS, real proxy env, real sockets).
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

from gatepath import diag_context
from gatepath.diag.probe import VpnDetail
from gatepath.portal_probe import ProbeResult
from gatepath.vpn_detector import VpnInterface


@pytest.fixture(autouse=True)
def _no_real_resolve1():
    """Guarantee no test reads the real systemd-resolved over D-Bus (the file's
    contract: nothing here touches the real system). `build_probe_context` calls
    `_detect_private_dns`, whose default source builds a dasbus proxy; patch that
    source to behave as if resolved/dasbus is absent. Tests that exercise
    detection directly inject their own `get_manager=` callable and never reach
    here; tests that assert wiring patch `_detect_private_dns` itself."""
    with mock.patch(
        "gatepath.diag_context._resolve1_manager",
        side_effect=RuntimeError("resolve1 unavailable"),
    ):
        yield


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


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="chmod does not restrict root, so this would pass vacuously",
)
def test_count_dns_servers_zero_for_unreadable_file(tmp_path) -> None:
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 1.1.1.1\n")
    resolv.chmod(0o000)
    try:
        assert diag_context._count_dns_servers(str(resolv)) == 0
    finally:
        resolv.chmod(0o644)  # restore so tmp_path cleanup can remove it


def test_count_dns_servers_survives_invalid_utf8(tmp_path) -> None:
    """A resolv.conf containing invalid UTF-8 must not raise. The valid
    `nameserver` line is still counted — only the garbage bytes on the
    other line are replaced during decoding, so real signal on an
    otherwise-corrupted file isn't discarded."""
    resolv = tmp_path / "resolv.conf"
    resolv.write_bytes(b"nameserver 1.1.1.1\n\xff\xfe invalid\n")
    assert diag_context._count_dns_servers(str(resolv)) == 1


# ---------------------------------------------------------------------------
# _detect_private_dns
# ---------------------------------------------------------------------------


# Fake `org.freedesktop.resolve1.Manager` proxies. The real proxy exposes
# `DNSOverTLS` (a string) and `CurrentDNSServer` (an `(iiay)` struct); these
# stand in for it so no test touches a real system bus.
import socket as _socket  # noqa: E402 — local alias for building AF_INET addresses


class _FakeManager:
    """A resolve1 Manager stand-in exposing `.DNSOverTLS` / `.CurrentDNSServer`.

    Omit `current_dns_server` to model the property being absent — accessing it
    then raises `AttributeError`, exactly as a real proxy would for a property
    the interface doesn't return.
    """

    _MISSING = object()

    def __init__(self, dns_over_tls, current_dns_server=_MISSING) -> None:
        self.DNSOverTLS = dns_over_tls
        if current_dns_server is not self._MISSING:
            self.CurrentDNSServer = current_dns_server


class _RaisingManager:
    """A Manager whose `DNSOverTLS` property read raises (simulates a D-Bus
    read error on the property itself)."""

    @property
    def DNSOverTLS(self):  # noqa: N802 — mirrors the D-Bus property name
        raise RuntimeError("dbus property read failed")


# An `(iiay)` CurrentDNSServer for 1.1.1.1: ifindex, AF_INET, raw address bytes.
_CURRENT_DNS_1111 = (2, _socket.AF_INET, bytes([1, 1, 1, 1]))
# A malformed CurrentDNSServer whose family/bytes cannot be formatted.
_CURRENT_DNS_BAD = (2, 999, b"\x01")


def test_detect_private_dns_strict_yes_returns_active_with_server() -> None:
    active, server = diag_context._detect_private_dns(
        get_manager=lambda: _FakeManager("yes", _CURRENT_DNS_1111)
    )
    assert active is True
    assert server == "1.1.1.1"


def test_detect_private_dns_strict_yes_case_insensitive() -> None:
    active, server = diag_context._detect_private_dns(
        get_manager=lambda: _FakeManager("YES", _CURRENT_DNS_1111)
    )
    assert active is True
    assert server == "1.1.1.1"


def test_detect_private_dns_strict_yes_server_none_when_absent() -> None:
    """`DNSOverTLS: yes` but `CurrentDNSServer` absent — still active, no host."""
    active, server = diag_context._detect_private_dns(get_manager=lambda: _FakeManager("yes"))
    assert active is True
    assert server is None


def test_detect_private_dns_strict_yes_server_none_when_unformattable() -> None:
    """A `CurrentDNSServer` that can't be formatted must NOT downgrade the strict
    DoT verdict — it returns `(True, None)`, never raises."""
    active, server = diag_context._detect_private_dns(
        get_manager=lambda: _FakeManager("yes", _CURRENT_DNS_BAD)
    )
    assert active is True
    assert server is None


def test_detect_private_dns_opportunistic_is_inactive() -> None:
    """Opportunistic DoT downgrades to plaintext, so it does not block captive DNS."""
    active, server = diag_context._detect_private_dns(
        get_manager=lambda: _FakeManager("opportunistic", _CURRENT_DNS_1111)
    )
    assert (active, server) == (False, None)


def test_detect_private_dns_plaintext_no_is_inactive() -> None:
    active, server = diag_context._detect_private_dns(
        get_manager=lambda: _FakeManager("no", _CURRENT_DNS_1111)
    )
    assert (active, server) == (False, None)


def test_detect_private_dns_empty_string_is_inactive() -> None:
    active, server = diag_context._detect_private_dns(get_manager=lambda: _FakeManager(""))
    assert (active, server) == (False, None)


def test_detect_private_dns_manager_unavailable_returns_inactive() -> None:
    """get_manager raising (dasbus/bus/resolved absent) → inactive, no raise."""
    def _missing() -> object:
        raise FileNotFoundError("no system bus")

    assert diag_context._detect_private_dns(get_manager=_missing) == (False, None)


def test_detect_private_dns_property_read_raises_returns_inactive() -> None:
    """A manager whose `.DNSOverTLS` access raises → inactive, no raise."""
    assert diag_context._detect_private_dns(get_manager=_RaisingManager) == (False, None)


def test_detect_private_dns_default_source_survives_missing_resolve1() -> None:
    """End-to-end: the real default source raising (no resolved/dasbus) still
    yields a benign inactive result."""
    with mock.patch(
        "gatepath.diag_context._resolve1_manager",
        side_effect=RuntimeError("resolve1 unavailable"),
    ):
        assert diag_context._detect_private_dns() == (False, None)


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
    # The probe is called exactly once during context assembly; both the
    # bypass derivation and `active_probe()` share that single cached result
    # (deliberate — it keeps the context internally consistent instead of
    # re-probing and possibly observing a different outcome).
    probe_fn.assert_called_once()
    for call in probe_fn.call_args_list:
        assert call.args[0] == "http://probe.test/" or call.kwargs.get("url") == "http://probe.test/"


def test_build_probe_context_wires_private_dns_active() -> None:
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ), mock.patch(
        "gatepath.diag_context._detect_private_dns", return_value=(True, "1.1.1.1")
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    assert ctx.private_dns_active is True
    assert ctx.private_dns_server == "1.1.1.1"


def test_build_probe_context_private_dns_inactive_by_default() -> None:
    with mock.patch("gatepath.vpn_detector.detect_vpn_details", return_value=[]), mock.patch(
        "gatepath.portal_probe.probe",
        return_value=ProbeResult(status="portal", portal_url="http://portal.test/"),
    ), mock.patch(
        "gatepath.diag_context._detect_private_dns", return_value=(False, None)
    ):
        ctx = diag_context.build_probe_context("wlan0", environ={})

    assert ctx.private_dns_active is False
    assert ctx.private_dns_server is None


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
        "private_dns",
        "http_proxy",
        "redirect_loop",
        "clock_skew",
        "https_only",
        "http",
    }
    assert len(engine._probes) == 9  # type: ignore[attr-defined]


def test_default_engine_includes_private_dns_probe() -> None:
    engine = diag_context.default_engine()
    names = {probe.name for probe in engine._probes}  # type: ignore[attr-defined]
    assert "private_dns" in names
