"""Tests for the diagnostics wiring in window.py.

GTK 4 + libadwaita are not importable in the unit sandbox (no ``Gtk`` 4.0
namespace), so the real ``GatepathWindow`` and its banner/panel widgets can
only be exercised where PyGObject is fully present. The decision logic the
window depends on is therefore factored into two pure, gi-free helpers —
``resolve_interface_name`` and ``vpn_labels_from_result`` — which are unit
tested here without a live display. A thin ``importorskip`` guard covers the
live-window path so it runs on a GTK-capable host without failing elsewhere.
"""

from __future__ import annotations

from typing import Optional

from gatepath.diag.engine import DiagnosisResult, ProbeCheck
from gatepath.diag.report import (
    NO_ACTION,
    DnsHijack,
    Healthy,
    Inconclusive,
    RecommendedAction,
    VpnBlocking,
)
from gatepath.portal_monitor import CaptiveInterfaceLookup
from gatepath.window import resolve_interface_name, vpn_labels_from_result


class _StubLookup(CaptiveInterfaceLookup):
    def __init__(self, interface: Optional[str]) -> None:
        self._interface = interface

    def get_captive_interface(self) -> Optional[str]:
        return self._interface


# ── resolve_interface_name ─────────────────────────────────────────────


def test_resolve_prefers_lookup_name_when_present() -> None:
    assert resolve_interface_name(_StubLookup("wlan0")) == "wlan0"


def test_resolve_falls_back_when_lookup_is_none() -> None:
    assert resolve_interface_name(None) == "(default route)"


def test_resolve_falls_back_when_lookup_returns_none() -> None:
    assert resolve_interface_name(_StubLookup(None)) == "(default route)"


def test_resolve_falls_back_on_empty_interface_name() -> None:
    # An empty string is not a usable label — fall back rather than surface "".
    assert resolve_interface_name(_StubLookup("")) == "(default route)"


# ── vpn_labels_from_result ─────────────────────────────────────────────


def _result(top: object, recommended: RecommendedAction = NO_ACTION) -> DiagnosisResult:
    return DiagnosisResult(
        top=top,  # type: ignore[arg-type]
        checks=(ProbeCheck(probe_name="probe", report=top),),  # type: ignore[arg-type]
        recommended=recommended,
    )


def test_vpn_labels_extracted_from_vpn_top() -> None:
    top = VpnBlocking(interface_name="tun0", is_full_tunnel=True)
    assert vpn_labels_from_result(_result(top)) == ["tun0"]


def test_vpn_labels_empty_for_healthy_top() -> None:
    assert vpn_labels_from_result(_result(Healthy())) == []


def test_vpn_labels_empty_for_non_vpn_finding() -> None:
    top = DnsHijack(host_probed="example.com", system_answer="1.2.3.4", doh_answer="5.6.7.8")
    assert vpn_labels_from_result(_result(top)) == []


def test_vpn_labels_empty_for_inconclusive_top() -> None:
    top = Inconclusive(probe_errors=("boom",))
    assert vpn_labels_from_result(_result(top)) == []


# ── live-window path (GTK-capable hosts only) ──────────────────────────


def test_on_diagnosis_result_drives_banner_and_panel() -> None:
    """On a GTK-capable host, a VPN top reveals the banner and renders the
    panel; a non-VPN top hides the banner. Skipped where GTK 4 is absent.
    """
    import pytest  # noqa: PLC0415

    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # type: ignore[import-untyped]  # noqa: PLC0415
    except (ValueError, ImportError):
        pytest.skip("GTK 4 / libadwaita namespace unavailable")

    from gatepath.window import GatepathWindow  # noqa: PLC0415

    Adw.init()
    app = Adw.Application(application_id="com.ventouxlabs.GatepathTest")
    window = GatepathWindow(application=app)

    # A VPN top reveals the banner and (lazily) renders the panel in place.
    vpn = VpnBlocking(interface_name="tun0", is_full_tunnel=True)
    window._on_diagnosis_result(_result(vpn))
    assert window._vpn_banner.get_revealed() is True
    assert window._diagnosis_panel is not None
    assert window._run_button.get_sensitive() is True

    # A second, non-VPN run re-renders in place and hides the banner again.
    first_panel = window._diagnosis_panel
    window._on_diagnosis_result(_result(Healthy()))
    assert window._vpn_banner.get_revealed() is False
    assert window._diagnosis_panel is first_panel
