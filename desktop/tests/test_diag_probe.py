"""Tests for the diagnostic probe protocol and context."""
from __future__ import annotations

import dataclasses

import pytest

from gatepath.diag.probe import HttpFetchResult, ProbeContext, VpnDetail
from gatepath.diag.report import Healthy


def make_context(**overrides: object) -> ProbeContext:
    defaults: dict[str, object] = {
        "interface_name": "wlan0",
        "probe_url": "http://portal.test/probe",
        "vpn_interfaces": (),
        "http_proxy_description": None,
        "dns_server_count": 1,
        "http_fetch": lambda url, accept: HttpFetchResult(None, None, None, None, "not wired"),
        "resolve_host": lambda host: (),
        "now_epoch_seconds": lambda: 0.0,
        "active_probe": lambda: None,
    }
    defaults.update(overrides)
    return ProbeContext(**defaults)  # type: ignore[arg-type]


def test_context_is_immutable() -> None:
    ctx = make_context()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.interface_name = "eth0"  # type: ignore[misc]


def test_context_defaults_are_inert() -> None:
    ctx = make_context()
    assert ctx.http_fetch("http://x", None).error == "not wired"
    assert ctx.resolve_host("x") == ()


def test_vpn_detail_carries_name_and_tunnel_mode() -> None:
    detail = VpnDetail(name="tun0", is_full_tunnel=True)
    assert detail.name == "tun0"
    assert detail.is_full_tunnel is True


def test_a_probe_satisfies_the_protocol_structurally() -> None:
    from gatepath.diag.probe import Probe

    class Stub:
        name = "stub"

        def run(self, ctx: ProbeContext) -> Healthy:
            return Healthy()

    probe: Probe = Stub()
    assert probe.run(make_context()).cause.value == "Healthy"
