"""Tests for the desktop diagnosis panel.

Two layers:

* The pure module-level helpers (`cause_label`, `report_detail`,
  `check_status`) run everywhere — they never import ``gi`` — and are
  exercised against every one of the ten cause shapes.
* The GTK widget (`DiagnosisPanel`) is only reachable when PyGObject is
  installed, so those tests ``pytest.importorskip("gi")`` and merely assert
  that ``render()`` on each cause shape does not raise.
"""

from __future__ import annotations

import dataclasses

import pytest

from gatepath.diag.engine import (
    DiagnosisResult,
    ProbeCheck,
    _recommended_action_for,
)
from gatepath.diag.report import (
    Cause,
    ClockSkew,
    DnsHijack,
    Healthy,
    HttpProxyBlocking,
    HttpsOnlyCaptive,
    Inconclusive,
    NO_ACTION,
    NoDnsServers,
    PortalRedirectLoop,
    PrivateDnsBlocking,
    VpnBlocking,
)
from gatepath.ui.diagnosis_panel import (
    _safe_markup,
    cause_label,
    check_status,
    report_detail,
)

# One representative report per cause. Keyed by cause so the parametrized
# tests below fail loudly the day a tenth cause is added without a fixture.
_REPORT_BY_CAUSE = {
    Cause.HEALTHY: Healthy(),
    Cause.VPN_BLOCKING: VpnBlocking(interface_name="tun0", is_full_tunnel=True),
    Cause.DNS_HIJACK: DnsHijack(
        host_probed="connectivitycheck.gstatic.com",
        system_answer="10.0.0.1",
        doh_answer="142.250.72.196",
    ),
    Cause.PRIVATE_DNS_BLOCKING: PrivateDnsBlocking(resolver_host="1.1.1.1"),
    Cause.HTTP_PROXY_BLOCKING: HttpProxyBlocking(description="proxy.corp:3128"),
    Cause.HTTPS_ONLY_CAPTIVE: HttpsOnlyCaptive(https_error_message="connection reset"),
    Cause.NO_DNS_SERVERS: NoDnsServers(),
    Cause.PORTAL_REDIRECT_LOOP: PortalRedirectLoop(
        chain=("http://a", "http://b", "http://a"),
    ),
    Cause.CLOCK_SKEW: ClockSkew(skew_seconds=900),
    Cause.INCONCLUSIVE: Inconclusive(probe_errors=("vpn: boom", "dns: timeout")),
}


def test_every_cause_has_a_fixture() -> None:
    # If this fails, a cause was added without extending the fixtures — the
    # rest of the file would then silently under-cover it.
    assert set(_REPORT_BY_CAUSE) == set(Cause)


# ── cause_label ────────────────────────────────────────────────────────


@pytest.mark.parametrize("cause", list(Cause))
def test_cause_label_is_nonempty_for_every_cause(cause: Cause) -> None:
    label = cause_label(cause)
    assert isinstance(label, str)
    assert label.strip()


def test_healthy_label_reads_benign() -> None:
    assert "no problem" in cause_label(Cause.HEALTHY).lower()


def test_cause_label_never_raises_on_unknown_input() -> None:
    # Degrade, don't crash, on a value that isn't a known Cause.
    assert isinstance(cause_label("something-else"), str)  # type: ignore[arg-type]


# ── report_detail ──────────────────────────────────────────────────────


@pytest.mark.parametrize("cause", list(Cause))
def test_report_detail_is_a_string_for_every_cause(cause: Cause) -> None:
    detail = report_detail(_REPORT_BY_CAUSE[cause])
    assert isinstance(detail, str)


def test_vpn_detail_names_the_interface() -> None:
    detail = report_detail(_REPORT_BY_CAUSE[Cause.VPN_BLOCKING])
    assert "tun0" in detail


def test_dns_hijack_detail_carries_both_answers() -> None:
    detail = report_detail(_REPORT_BY_CAUSE[Cause.DNS_HIJACK])
    assert "10.0.0.1" in detail
    assert "142.250.72.196" in detail


def test_proxy_detail_carries_description() -> None:
    detail = report_detail(_REPORT_BY_CAUSE[Cause.HTTP_PROXY_BLOCKING])
    assert "proxy.corp:3128" in detail


def test_https_only_detail_carries_error() -> None:
    detail = report_detail(_REPORT_BY_CAUSE[Cause.HTTPS_ONLY_CAPTIVE])
    assert "connection reset" in detail


def test_redirect_loop_detail_reports_hop_count() -> None:
    detail = report_detail(_REPORT_BY_CAUSE[Cause.PORTAL_REDIRECT_LOOP])
    assert "3" in detail


def test_report_detail_preserves_raw_markup_chars() -> None:
    """``report_detail`` returns raw text, angle brackets and all.

    urllib error strings look like ``<urlopen error [Errno -2] ...>``. The
    detail helper must NOT pre-escape them — escaping is the GTK boundary's job
    (``DiagnosisPanel`` wraps every subtitle in ``GLib.markup_escape_text``).
    This pins the contract so the escape at the render site can't be quietly
    dropped: if this helper started escaping, subtitles would be double-escaped.
    """
    detail = report_detail(HttpsOnlyCaptive(https_error_message="<urlopen error boom>"))
    assert "<urlopen error boom>" in detail


# ── _safe_markup (the escaping the panel applies at the subtitle boundary) ──


def test_safe_markup_escapes_angle_brackets() -> None:
    # The exact failure the fix targets: a urllib error string as a subtitle.
    assert _safe_markup("<urlopen error [Errno -2]>") == "&lt;urlopen error [Errno -2]&gt;"


def test_safe_markup_escapes_ampersand() -> None:
    assert _safe_markup("a & b") == "a &amp; b"


def test_safe_markup_leaves_quotes_untouched() -> None:
    # Subtitles are Pango *element* text, not attribute values, so quotes need
    # no escaping — and over-escaping them would render literal &#x27; to users.
    assert _safe_markup("it's \"fine\"") == "it's \"fine\""


def test_safe_markup_is_a_noop_for_plain_text() -> None:
    assert _safe_markup("Interface tun0 (full-tunnel)") == "Interface tun0 (full-tunnel)"


def test_clock_skew_detail_reports_minutes() -> None:
    detail = report_detail(_REPORT_BY_CAUSE[Cause.CLOCK_SKEW])
    assert "15" in detail  # 900s // 60


def test_inconclusive_detail_joins_probe_errors() -> None:
    detail = report_detail(_REPORT_BY_CAUSE[Cause.INCONCLUSIVE])
    assert "vpn: boom" in detail
    assert "dns: timeout" in detail


def test_report_detail_never_raises_on_unknown_shape() -> None:
    @dataclasses.dataclass(frozen=True)
    class _Alien:
        cause = "Alien"

    assert isinstance(report_detail(_Alien()), str)  # type: ignore[arg-type]


# ── check_status ───────────────────────────────────────────────────────


def test_check_status_maps_healthy_to_pass() -> None:
    assert check_status(Cause.HEALTHY) == "pass"


def test_check_status_maps_inconclusive() -> None:
    assert check_status(Cause.INCONCLUSIVE) == "inconclusive"


@pytest.mark.parametrize(
    "cause",
    [c for c in Cause if c not in (Cause.HEALTHY, Cause.INCONCLUSIVE)],
)
def test_check_status_maps_everything_else_to_fail(cause: Cause) -> None:
    assert check_status(cause) == "fail"


# ── GTK widget (only when PyGObject is present) ────────────────────────


def _result_for(cause: Cause) -> DiagnosisResult:
    """A DiagnosisResult whose `top` is the given cause, plus one check per
    cause so every row shape gets rendered too."""
    top = _REPORT_BY_CAUSE[cause]
    checks = tuple(
        ProbeCheck(probe_name=f"{c.value}Probe", report=report)
        for c, report in _REPORT_BY_CAUSE.items()
    )
    return DiagnosisResult(
        top=top,
        checks=checks,
        recommended=_recommended_action_for(top),
    )


@pytest.mark.parametrize("cause", list(Cause))
def test_render_does_not_raise_for_any_cause(cause: Cause) -> None:
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # noqa: PLC0415
    except (ValueError, ImportError):
        pytest.skip("GTK 4 / libadwaita runtime not available")

    Adw.init()
    from gatepath.ui.diagnosis_panel import DiagnosisPanel  # noqa: PLC0415

    panel = DiagnosisPanel()
    # Render twice to prove re-render on a repeated manual run is safe.
    panel.render(_result_for(cause))
    panel.render(_result_for(cause))


def test_render_empty_checks_does_not_raise() -> None:
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # noqa: PLC0415
    except (ValueError, ImportError):
        pytest.skip("GTK 4 / libadwaita runtime not available")

    Adw.init()
    from gatepath.ui.diagnosis_panel import DiagnosisPanel  # noqa: PLC0415

    panel = DiagnosisPanel()
    panel.render(DiagnosisResult(top=Healthy(), checks=(), recommended=NO_ACTION))
