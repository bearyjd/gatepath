"""End-to-end tests for SessionController.

Verifies the contract that the desktop session timeout actually MATERIALISES an
audit entry — closing the gap from PR #1 review H1 where the timer fired into
a no-op stub.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gatepath.audit_log import read_all
from gatepath.portal_session import (
    CloseReason,
    PortalPhase,
    PortalSession,
    to_active,
    to_detected,
)
from gatepath.session_controller import SessionController


def _make_active_session() -> PortalSession:
    """Build an Active session via the proper transition path."""
    s = PortalSession()
    s = s.transition_or_none(PortalPhase.MONITORING)
    assert s is not None
    s = to_detected(
        s,
        ssid="Cafe-WiFi",
        gateway_ip="192.168.1.1",
        portal_url="http://portal.cafe.example/login",
        portal_domain="portal.cafe.example",
        vpn_interfaces_detected=[],
        vpn_warning_shown=False,
    )
    assert s is not None
    s = to_active(s)
    assert s is not None
    return s


class TestTimeoutMaterialisesAuditEntry:
    """The timer fires → controller.on_timeout() → audit entry on disk.
    This is the H1 contract — without it the desktop "10-minute timeout"
    is a documented lie.
    """

    def test_on_timeout_writes_audit_entry_with_TIMEOUT_reason(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "audit.jsonl"
        controller = SessionController(audit_log_path=log)
        controller.set_active(_make_active_session())

        result = controller.on_timeout()

        assert result is not None
        assert result.close_reason == CloseReason.TIMEOUT
        entries = read_all(log_path=log)
        assert len(entries) == 1
        assert entries[0]["close_reason"] == "timeout"
        assert entries[0]["portal_domain"] == "portal.cafe.example"

    def test_on_timeout_fires_on_close_callback(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        callback_seen: list[PortalSession] = []
        controller = SessionController(
            audit_log_path=log,
            on_close=callback_seen.append,
        )
        controller.set_active(_make_active_session())
        controller.on_timeout()
        assert len(callback_seen) == 1
        assert callback_seen[0].close_reason == CloseReason.TIMEOUT

    def test_on_timeout_with_no_session_is_safe_noop(
        self, tmp_path: Path
    ) -> None:
        """Reaching this branch means a stale timer fired after dismiss —
        must not raise, must not write."""
        log = tmp_path / "audit.jsonl"
        controller = SessionController(audit_log_path=log)
        result = controller.on_timeout()
        assert result is None
        assert read_all(log_path=log) == []


class TestUserDismissAndCompleted:
    def test_user_dismiss_writes_USER_DISMISSED_entry(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "audit.jsonl"
        controller = SessionController(audit_log_path=log)
        controller.set_active(_make_active_session())

        controller.on_user_dismiss()

        entries = read_all(log_path=log)
        assert entries[0]["close_reason"] == "user_dismissed"

    def test_portal_completed_writes_PORTAL_COMPLETED_entry(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "audit.jsonl"
        controller = SessionController(audit_log_path=log)
        controller.set_active(_make_active_session())

        controller.on_portal_completed()

        entries = read_all(log_path=log)
        assert entries[0]["close_reason"] == "portal_completed"


class TestIdempotency:
    def test_close_after_close_is_safe_noop(self, tmp_path: Path) -> None:
        """A double-close (e.g., user dismisses then timer fires late) must
        not write a second audit entry."""
        log = tmp_path / "audit.jsonl"
        controller = SessionController(audit_log_path=log)
        controller.set_active(_make_active_session())

        controller.on_user_dismiss()
        result = controller.on_timeout()  # late timer

        assert result is None
        entries = read_all(log_path=log)
        assert len(entries) == 1
        assert entries[0]["close_reason"] == "user_dismissed"


class TestCounters:
    def test_blocked_navigation_counter_persists_through_close(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "audit.jsonl"
        controller = SessionController(audit_log_path=log)
        controller.set_active(_make_active_session())

        controller.record_blocked_navigation()
        controller.record_blocked_navigation()
        controller.record_blocked_resource()

        controller.on_user_dismiss()

        entries = read_all(log_path=log)
        assert entries[0]["blocked_navigation_attempts"] == 2
        assert entries[0]["blocked_resource_requests"] == 1

    def test_record_after_close_is_safe_noop(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        controller = SessionController(audit_log_path=log)
        controller.set_active(_make_active_session())
        controller.on_user_dismiss()
        controller.record_blocked_navigation()  # no-op, no exception
        # Counters frozen at close time.
        entries = read_all(log_path=log)
        assert entries[0]["blocked_navigation_attempts"] == 0


class TestSetActiveValidation:
    def test_set_active_rejects_non_Active_phase(self) -> None:
        controller = SessionController()
        with pytest.raises(ValueError, match="phase=ACTIVE"):
            controller.set_active(PortalSession())  # IDLE
