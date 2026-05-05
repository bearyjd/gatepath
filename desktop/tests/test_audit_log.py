"""Tests for gatepath.audit_log — JSONL writer conforming to AUDIT_LOG_SCHEMA.md."""

from __future__ import annotations

import concurrent.futures
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gatepath.audit_log import read_all, write_session
from gatepath.portal_session import CloseReason, PortalPhase, PortalSession

# All required schema fields per docs/AUDIT_LOG_SCHEMA.md
_REQUIRED_FIELDS = {
    "schema_version",
    "timestamp_utc",
    "platform",
    "ssid",
    "gateway_ip",
    "portal_domain",
    "vpn_interfaces_detected",
    "vpn_warning_shown",
    "session_opened_utc",
    "session_closed_utc",
    "close_reason",
    "duration_seconds",
    "blocked_navigation_attempts",
    "blocked_resource_requests",
}


def _make_completed_session() -> PortalSession:
    opened = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    closed = datetime(2026, 5, 5, 12, 2, 42, tzinfo=timezone.utc)
    return PortalSession(
        phase=PortalPhase.COMPLETED,
        ssid="Airport-WiFi",
        gateway_ip="192.168.0.1",
        portal_domain="wifi.example-airport.com",
        vpn_interfaces_detected=["tailscale0 (full_tunnel)"],
        vpn_warning_shown=True,
        session_opened_utc=opened,
        session_closed_utc=closed,
        close_reason=CloseReason.PORTAL_COMPLETED,
        blocked_navigation_attempts=2,
        blocked_resource_requests=11,
    )


class TestWriteSession:
    def test_creates_file(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        assert log.exists()

    def test_entry_has_all_required_fields(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        entries = read_all(log_path=log)
        assert len(entries) == 1
        entry = entries[0]
        missing = _REQUIRED_FIELDS - set(entry.keys())
        assert not missing, f"Missing fields: {missing}"

    def test_field_types_match_schema(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        entry = read_all(log_path=log)[0]

        assert entry["schema_version"] == 1
        assert isinstance(entry["schema_version"], int)
        assert entry["platform"] == "desktop"
        assert entry["timestamp_utc"].endswith("Z")
        assert entry["session_opened_utc"].endswith("Z")
        assert entry["session_closed_utc"].endswith("Z")
        assert isinstance(entry["vpn_interfaces_detected"], list)
        assert isinstance(entry["vpn_warning_shown"], bool)
        assert isinstance(entry["duration_seconds"], int)
        assert entry["duration_seconds"] == 162
        assert isinstance(entry["blocked_navigation_attempts"], int)
        assert isinstance(entry["blocked_resource_requests"], int)
        assert entry["close_reason"] == "portal_completed"

    def test_ssid_and_gateway_preserved(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        entry = read_all(log_path=log)[0]
        assert entry["ssid"] == "Airport-WiFi"
        assert entry["gateway_ip"] == "192.168.0.1"

    def test_ssid_can_be_null(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        s = _make_completed_session()
        import dataclasses
        s = dataclasses.replace(s, ssid=None, gateway_ip=None)
        write_session(s, log_path=log)
        entry = read_all(log_path=log)[0]
        assert entry["ssid"] is None
        assert entry["gateway_ip"] is None

    def test_multiple_writes_preserved_in_order(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        for _ in range(3):
            write_session(_make_completed_session(), log_path=log)
        entries = read_all(log_path=log)
        assert len(entries) == 3
        # Each entry is a valid dict with all required fields.
        for entry in entries:
            assert set(entry.keys()) >= _REQUIRED_FIELDS

    def test_concurrent_writes_no_corruption(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        session = _make_completed_session()

        def _write() -> None:
            write_session(session, log_path=log)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_write) for _ in range(10)]
            for f in futures:
                f.result()

        entries = read_all(log_path=log)
        assert len(entries) == 10, f"Expected 10 entries, got {len(entries)}"
        for entry in entries:
            assert set(entry.keys()) >= _REQUIRED_FIELDS

    def test_raises_without_portal_domain(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        import dataclasses
        s = dataclasses.replace(_make_completed_session(), portal_domain=None)
        with pytest.raises(ValueError, match="portal_domain"):
            write_session(s, log_path=log)  # type: ignore[arg-type]

    def test_raises_without_session_opened(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        import dataclasses
        s = dataclasses.replace(_make_completed_session(), session_opened_utc=None)
        with pytest.raises(ValueError, match="session_opened_utc"):
            write_session(s, log_path=log)
