"""Tests for the network-touching desktop probes: redirect_loop, clock_skew,
https_only.

Mirrors of the Android tests in
`android/app/src/test/java/com/ventouxlabs/gatepath/diag/`:
RedirectLoopProbeTest.kt, ClockSkewProbeTest.kt, HttpsOnlyProbeTest.kt.
"""
from __future__ import annotations

from typing import Callable

from gatepath.diag.clock_skew_probe import ClockSkewProbe
from gatepath.diag.https_only_probe import HttpsOnlyProbe
from gatepath.diag.probe import HttpFetchResult, ProbeContext
from gatepath.diag.redirect_loop_probe import RedirectLoopProbe
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


def _redirect(to: str) -> HttpFetchResult:
    return HttpFetchResult(status_code=302, location=to, date_epoch_seconds=None, body=None, error=None)


def _ok_204() -> HttpFetchResult:
    return HttpFetchResult(status_code=204, location=None, date_epoch_seconds=None, body=None, error=None)


def _page_200() -> HttpFetchResult:
    return HttpFetchResult(status_code=200, location=None, date_epoch_seconds=None, body="<html>portal</html>", error=None)


def _error(message: str) -> HttpFetchResult:
    return HttpFetchResult(status_code=None, location=None, date_epoch_seconds=None, body=None, error=message)


def _fetch_from(responses: dict[str, HttpFetchResult]) -> Callable[[str, object], HttpFetchResult]:
    def _fetch(url: str, accept: object) -> HttpFetchResult:
        return responses.get(url, _error(f"unexpected url: {url}"))

    return _fetch


# --- RedirectLoopProbe ---


def test_redirect_loop_two_node_cycle_detected_with_exact_chain() -> None:
    responses = {
        "http://portal.test/probe": _redirect("http://portal.test/a"),
        "http://portal.test/a": _redirect("http://portal.test/b"),
        "http://portal.test/b": _redirect("http://portal.test/a"),
    }
    report = RedirectLoopProbe().run(ctx(http_fetch=_fetch_from(responses)))
    assert report.cause is Cause.PORTAL_REDIRECT_LOOP
    assert report.chain == (
        "http://portal.test/probe",
        "http://portal.test/a",
        "http://portal.test/b",
        "http://portal.test/a",
    )


def test_redirect_loop_relative_location_resolved_against_current_url() -> None:
    responses = {
        "http://portal.test/probe": _redirect("/a"),
        "http://portal.test/a": _redirect("/a"),
    }
    report = RedirectLoopProbe().run(ctx(http_fetch=_fetch_from(responses)))
    assert report.cause is Cause.PORTAL_REDIRECT_LOOP


def test_redirect_loop_chain_ending_in_page_is_healthy() -> None:
    responses = {
        "http://portal.test/probe": _redirect("http://portal.test/portal"),
        "http://portal.test/portal": _page_200(),
    }
    report = RedirectLoopProbe().run(ctx(http_fetch=_fetch_from(responses)))
    assert report.cause is Cause.HEALTHY


def test_redirect_loop_validated_204_is_healthy() -> None:
    responses = {"http://portal.test/probe": _ok_204()}
    report = RedirectLoopProbe().run(ctx(http_fetch=_fetch_from(responses)))
    assert report.cause is Cause.HEALTHY


def test_redirect_loop_first_fetch_error_is_inconclusive() -> None:
    responses = {"http://portal.test/probe": _error("connect timed out")}
    report = RedirectLoopProbe().run(ctx(http_fetch=_fetch_from(responses)))
    assert report.cause is Cause.INCONCLUSIVE
    assert "connect timed out" in report.probe_errors[0]


def test_redirect_loop_mid_chain_error_is_healthy() -> None:
    responses = {
        "http://portal.test/probe": _redirect("http://portal.test/a"),
        "http://portal.test/a": _error("connection reset"),
    }
    report = RedirectLoopProbe().run(ctx(http_fetch=_fetch_from(responses)))
    assert report.cause is Cause.HEALTHY


def test_redirect_loop_long_non_repeating_chain_gives_up_healthy_at_hop_cap() -> None:
    responses = {
        "http://portal.test/probe": _redirect("http://portal.test/1"),
        "http://portal.test/1": _redirect("http://portal.test/2"),
        "http://portal.test/2": _redirect("http://portal.test/3"),
        "http://portal.test/3": _redirect("http://portal.test/4"),
        "http://portal.test/4": _redirect("http://portal.test/5"),
        "http://portal.test/5": _redirect("http://portal.test/6"),
    }
    report = RedirectLoopProbe().run(ctx(http_fetch=_fetch_from(responses)))
    assert report.cause is Cause.HEALTHY


def test_redirect_loop_declines_when_default_route_bypasses_captive() -> None:
    called = {"fetch": False}

    def fetch(url: str, accept: object) -> HttpFetchResult:
        called["fetch"] = True
        return _redirect("http://portal.test/a")

    report = RedirectLoopProbe().run(
        ctx(http_fetch=fetch, default_route_bypasses_captive=True)
    )
    assert report.cause is Cause.INCONCLUSIVE
    assert "default route is not the captive network" in report.probe_errors[0]
    assert called["fetch"] is False


# --- ClockSkewProbe ---

_NOW_SECONDS = 1_800_000_000.0


def test_clock_skew_device_ahead_reports_positive_skew() -> None:
    responses = {
        "http://portal.test/probe": HttpFetchResult(302, "http://portal.test/portal", _NOW_SECONDS - 900, None, None),
    }
    report = ClockSkewProbe().run(
        ctx(http_fetch=_fetch_from(responses), now_epoch_seconds=lambda: _NOW_SECONDS)
    )
    assert report.cause is Cause.CLOCK_SKEW
    assert report.skew_seconds == 900


def test_clock_skew_device_behind_reports_positive_skew() -> None:
    responses = {
        "http://portal.test/probe": HttpFetchResult(302, "http://portal.test/portal", _NOW_SECONDS + 900, None, None),
    }
    report = ClockSkewProbe().run(
        ctx(http_fetch=_fetch_from(responses), now_epoch_seconds=lambda: _NOW_SECONDS)
    )
    assert report.cause is Cause.CLOCK_SKEW
    assert report.skew_seconds == 900


def test_clock_skew_within_tolerance_is_healthy() -> None:
    responses = {
        "http://portal.test/probe": HttpFetchResult(302, "http://portal.test/portal", _NOW_SECONDS - 200, None, None),
    }
    report = ClockSkewProbe().run(
        ctx(http_fetch=_fetch_from(responses), now_epoch_seconds=lambda: _NOW_SECONDS)
    )
    assert report.cause is Cause.HEALTHY


def test_clock_skew_missing_date_header_is_healthy() -> None:
    responses = {
        "http://portal.test/probe": HttpFetchResult(302, "http://portal.test/portal", None, None, None),
    }
    report = ClockSkewProbe().run(
        ctx(http_fetch=_fetch_from(responses), now_epoch_seconds=lambda: _NOW_SECONDS)
    )
    assert report.cause is Cause.HEALTHY


def test_clock_skew_fetch_error_is_healthy() -> None:
    responses = {"http://portal.test/probe": _error("timeout")}
    report = ClockSkewProbe().run(
        ctx(http_fetch=_fetch_from(responses), now_epoch_seconds=lambda: _NOW_SECONDS)
    )
    assert report.cause is Cause.HEALTHY


# --- HttpsOnlyProbe ---


def test_https_only_fires_when_http_validated_and_https_errors() -> None:
    fetched_url = {}

    def fetch(url: str, accept: object) -> HttpFetchResult:
        fetched_url["url"] = url
        return _error("Connection reset")

    report = HttpsOnlyProbe().run(
        ctx(http_fetch=fetch, active_probe=lambda: ProbeResult(status="validated"))
    )
    assert report.cause is Cause.HTTPS_ONLY_CAPTIVE
    assert report.https_error_message == "Connection reset"
    assert fetched_url["url"] == "https://portal.test/probe"


def test_https_only_healthy_when_http_and_https_both_work() -> None:
    report = HttpsOnlyProbe().run(
        ctx(http_fetch=lambda url, accept: _ok_204(), active_probe=lambda: ProbeResult(status="validated"))
    )
    assert report.cause is Cause.HEALTHY


def test_https_only_healthy_when_http_still_captive() -> None:
    called = {"fetch": False}

    def fetch(url: str, accept: object) -> HttpFetchResult:
        called["fetch"] = True
        return _error("reset")

    report = HttpsOnlyProbe().run(
        ctx(
            http_fetch=fetch,
            active_probe=lambda: ProbeResult(status="portal", portal_url="http://portal.test/portal"),
        )
    )
    assert report.cause is Cause.HEALTHY
    assert called["fetch"] is False


def test_https_only_healthy_when_http_errors() -> None:
    report = HttpsOnlyProbe().run(
        ctx(
            http_fetch=lambda url, accept: _error("reset"),
            active_probe=lambda: ProbeResult(status="error", message="EPERM"),
        )
    )
    assert report.cause is Cause.HEALTHY


def test_https_only_declines_when_default_route_bypasses_captive() -> None:
    called = {"active_probe": False}

    def active_probe() -> ProbeResult:
        called["active_probe"] = True
        return ProbeResult(status="validated")

    report = HttpsOnlyProbe().run(
        ctx(active_probe=active_probe, default_route_bypasses_captive=True)
    )
    assert report.cause is Cause.INCONCLUSIVE
    assert "default route is not the captive network" in report.probe_errors[0]
    assert called["active_probe"] is False
