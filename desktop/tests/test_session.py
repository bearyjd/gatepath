"""Tests for gatepath.portal_session state machine."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from gatepath.portal_session import (
    CloseReason,
    PortalPhase,
    PortalSession,
    SESSION_TIMEOUT_SECONDS,
    to_active,
    to_aborted_pre_active,
    to_completed,
    to_detected,
)


class TestValidTransitions:
    def test_idle_to_monitoring(self) -> None:
        s = PortalSession()
        result = s.transition_or_none(PortalPhase.MONITORING)
        assert result is not None
        assert result.phase == PortalPhase.MONITORING

    def test_monitoring_to_detected(self) -> None:
        s = PortalSession(phase=PortalPhase.MONITORING)
        result = s.transition_or_none(PortalPhase.DETECTED)
        assert result is not None
        assert result.phase == PortalPhase.DETECTED

    def test_detected_to_active(self) -> None:
        s = PortalSession(phase=PortalPhase.DETECTED)
        result = s.transition_or_none(PortalPhase.ACTIVE)
        assert result is not None
        assert result.phase == PortalPhase.ACTIVE

    def test_active_to_completed(self) -> None:
        s = PortalSession(phase=PortalPhase.ACTIVE)
        result = s.transition_or_none(PortalPhase.COMPLETED)
        assert result is not None
        assert result.phase == PortalPhase.COMPLETED


class TestInvalidTransitions:
    def test_active_to_active_returns_none(self) -> None:
        s = PortalSession(phase=PortalPhase.ACTIVE)
        assert s.transition_or_none(PortalPhase.ACTIVE) is None

    def test_completed_to_monitoring_returns_none(self) -> None:
        s = PortalSession(phase=PortalPhase.COMPLETED)
        assert s.transition_or_none(PortalPhase.MONITORING) is None

    def test_idle_to_completed_returns_none(self) -> None:
        s = PortalSession()
        assert s.transition_or_none(PortalPhase.COMPLETED) is None


class TestToDetected:
    def test_attaches_network_context(self) -> None:
        s = PortalSession(phase=PortalPhase.MONITORING)
        result = to_detected(
            s,
            ssid="Airport-WiFi",
            gateway_ip="192.168.0.1",
            portal_url="http://portal.example.com/login",
            portal_domain="portal.example.com",
            vpn_interfaces_detected=["tailscale0 (full_tunnel)"],
            vpn_warning_shown=True,
        )
        assert result is not None
        assert result.phase == PortalPhase.DETECTED
        assert result.ssid == "Airport-WiFi"
        assert result.gateway_ip == "192.168.0.1"
        assert result.portal_domain == "portal.example.com"
        assert result.vpn_warning_shown is True

    def test_vpn_warning_list_preserved(self) -> None:
        s = PortalSession(phase=PortalPhase.MONITORING)
        result = to_detected(
            s,
            ssid=None,
            gateway_ip=None,
            portal_url="http://x.com/",
            portal_domain="x.com",
            vpn_interfaces_detected=["tailscale0 (full_tunnel)"],
            vpn_warning_shown=True,
        )
        assert result is not None
        assert result.vpn_interfaces_detected == ["tailscale0 (full_tunnel)"]

    def test_from_wrong_phase_returns_none(self) -> None:
        s = PortalSession(phase=PortalPhase.IDLE)
        result = to_detected(
            s,
            ssid=None,
            gateway_ip=None,
            portal_url="http://x.com/",
            portal_domain="x.com",
            vpn_interfaces_detected=[],
            vpn_warning_shown=False,
        )
        assert result is None


class TestToActive:
    def test_records_open_timestamp(self) -> None:
        s = PortalSession(phase=PortalPhase.DETECTED)
        result = to_active(s)
        assert result is not None
        assert result.phase == PortalPhase.ACTIVE
        assert result.session_opened_utc is not None
        assert result.session_opened_utc.tzinfo is not None


class TestToCompleted:
    def test_portal_completed_close_reason(self) -> None:
        opened = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        s = PortalSession(phase=PortalPhase.ACTIVE, session_opened_utc=opened)
        result = to_completed(
            s,
            reason=CloseReason.PORTAL_COMPLETED,
            blocked_nav=2,
            blocked_resources=11,
        )
        assert result is not None
        assert result.phase == PortalPhase.COMPLETED
        assert result.close_reason == CloseReason.PORTAL_COMPLETED
        assert result.blocked_navigation_attempts == 2
        assert result.blocked_resource_requests == 11
        assert result.session_closed_utc is not None

    def test_timeout_close_reason(self) -> None:
        opened = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        s = PortalSession(phase=PortalPhase.ACTIVE, session_opened_utc=opened)
        result = to_completed(
            s,
            reason=CloseReason.TIMEOUT,
            blocked_nav=0,
            blocked_resources=0,
        )
        assert result is not None
        assert result.close_reason == CloseReason.TIMEOUT

    def test_error_close_reason_goes_to_error_phase(self) -> None:
        opened = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        s = PortalSession(phase=PortalPhase.ACTIVE, session_opened_utc=opened)
        result = to_completed(
            s,
            reason=CloseReason.ERROR,
            blocked_nav=0,
            blocked_resources=0,
        )
        assert result is not None
        assert result.phase == PortalPhase.ERROR


class TestBlockedCounters:
    def test_increment_via_replace(self) -> None:
        s = PortalSession(phase=PortalPhase.ACTIVE)
        s2 = dataclasses.replace(s, blocked_navigation_attempts=s.blocked_navigation_attempts + 1)
        s3 = dataclasses.replace(s2, blocked_resource_requests=s2.blocked_resource_requests + 5)
        assert s3.blocked_navigation_attempts == 1
        assert s3.blocked_resource_requests == 5
        # Original is unchanged.
        assert s.blocked_navigation_attempts == 0


class TestDurationSeconds:
    def test_returns_none_when_not_closed(self) -> None:
        s = PortalSession(
            session_opened_utc=datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        )
        assert s.duration_seconds is None

    def test_calculates_correctly(self) -> None:
        opened = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        closed = datetime(2026, 5, 5, 12, 2, 42, tzinfo=timezone.utc)
        s = PortalSession(session_opened_utc=opened, session_closed_utc=closed)
        assert s.duration_seconds == 162


class TestCloseReasonEnum:
    def test_values_match_schema(self) -> None:
        assert CloseReason.PORTAL_COMPLETED == "portal_completed"
        assert CloseReason.USER_DISMISSED == "user_dismissed"
        assert CloseReason.TIMEOUT == "timeout"
        assert CloseReason.ERROR == "error"
        assert CloseReason.ABORTED_PRE_ACTIVE == "aborted_pre_active"


class TestSessionTimeoutConstant:
    def test_is_ten_minutes(self) -> None:
        assert SESSION_TIMEOUT_SECONDS == 600


class TestToAbortedPreActive:
    """Recovery path for sessions that never reached ACTIVE.

    Replaces the prior behaviour where `close_reason` could be None — the
    audit log writer now requires a non-null close_reason on every entry.
    """

    def test_from_detected_sets_close_reason(self) -> None:
        s = PortalSession(
            phase=PortalPhase.DETECTED,
            portal_domain="x.com",
            portal_url="http://x.com/",
        )
        result = to_aborted_pre_active(s)
        assert result.phase == PortalPhase.COMPLETED
        assert result.close_reason == CloseReason.ABORTED_PRE_ACTIVE
        assert result.session_opened_utc is not None
        assert result.session_closed_utc is not None
        # Duration is 0 — open and close are stamped at the same moment.
        assert result.duration_seconds == 0

    def test_from_monitoring_also_works(self) -> None:
        """The recovery path is intentionally non-strict — any pre-Active phase."""
        s = PortalSession(phase=PortalPhase.MONITORING, portal_domain="x.com")
        result = to_aborted_pre_active(s)
        assert result.close_reason == CloseReason.ABORTED_PRE_ACTIVE

    def test_preserves_existing_open_timestamp(self) -> None:
        opened = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        s = PortalSession(
            phase=PortalPhase.DETECTED,
            portal_domain="x.com",
            session_opened_utc=opened,
        )
        result = to_aborted_pre_active(s)
        assert result.session_opened_utc == opened
        assert result.session_closed_utc is not None
        assert result.session_closed_utc >= opened

    def test_immutability(self) -> None:
        """Original session must not be mutated."""
        s = PortalSession(phase=PortalPhase.DETECTED, portal_domain="x.com")
        _ = to_aborted_pre_active(s)
        assert s.phase == PortalPhase.DETECTED
        assert s.close_reason is None
