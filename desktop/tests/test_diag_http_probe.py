"""Tests for HttpProbe.

Mirror of the Android test in
`android/app/src/test/java/com/ventouxlabs/gatepath/diag/HttpProbeTest.kt`.
"""
from __future__ import annotations

from gatepath.diag.http_probe import HttpProbe
from gatepath.diag.probe import HttpFetchResult, ProbeContext
from gatepath.diag.report import Cause
from gatepath.portal_probe import ProbeResult


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
        "active_probe": lambda: ProbeResult(status="validated"),
    }
    defaults.update(overrides)
    return ProbeContext(**defaults)  # type: ignore[arg-type]


def test_http_probe_healthy_when_validated() -> None:
    report = HttpProbe().run(ctx(active_probe=lambda: ProbeResult(status="validated")))
    assert report.cause is Cause.HEALTHY


def test_http_probe_healthy_when_portal() -> None:
    report = HttpProbe().run(
        ctx(active_probe=lambda: ProbeResult(status="portal", portal_url="http://portal.test/portal"))
    )
    assert report.cause is Cause.HEALTHY


def test_http_probe_inconclusive_when_error_carries_message() -> None:
    report = HttpProbe().run(ctx(active_probe=lambda: ProbeResult(status="error", message="EPERM")))
    assert report.cause is Cause.INCONCLUSIVE
    assert "EPERM" in report.probe_errors[0]
