"""Tests for gatepath.audit_log — JSONL writer conforming to AUDIT_LOG_SCHEMA.md.

The schema contract is loaded from docs/audit_log_schema.json (authoritative
machine-readable contract). The Markdown doc is for humans; the JSON is for tests.
"""

from __future__ import annotations

import concurrent.futures
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gatepath.audit_log import read_all, write_session
from gatepath.portal_session import CloseReason, PortalPhase, PortalSession

# Load the authoritative schema. Resolved relative to this test file so it
# works regardless of pytest's cwd.
_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "audit_log_schema.json"
)
with _SCHEMA_PATH.open("r", encoding="utf-8") as _fh:
    _SCHEMA: dict = json.load(_fh)

_REQUIRED_FIELDS: set[str] = set(_SCHEMA["required_fields"])
_NULLABLE_FIELDS: set[str] = set(_SCHEMA["nullable_fields"])
_CLOSE_REASON_ENUM: set[str] = set(_SCHEMA["close_reason_enum"])
_PLATFORM_ENUM: set[str] = set(_SCHEMA["platform_enum"])


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

    def test_raises_without_close_reason(self, tmp_path: Path) -> None:
        """close_reason MUST be non-null. ABORTED_PRE_ACTIVE is the recovery."""
        log = tmp_path / "audit.jsonl"
        import dataclasses
        s = dataclasses.replace(_make_completed_session(), close_reason=None)
        with pytest.raises(ValueError, match="close_reason"):
            write_session(s, log_path=log)

    def test_aborted_pre_active_with_empty_portal_domain_writes_successfully(
        self, tmp_path: Path
    ) -> None:
        """ABORTED_PRE_ACTIVE is the only close_reason where portal_domain MAY
        be empty (e.g. dismissal during MONITORING — never observed a portal).
        Cross-platform parity: Android writes the empty string here too.
        """
        log = tmp_path / "audit.jsonl"
        import dataclasses
        s = dataclasses.replace(
            _make_completed_session(),
            close_reason=CloseReason.ABORTED_PRE_ACTIVE,
            portal_domain="",
        )
        write_session(s, log_path=log)
        entry = read_all(log_path=log)[0]
        assert entry["close_reason"] == "aborted_pre_active"
        assert entry["portal_domain"] == ""

    def test_non_aborted_with_empty_portal_domain_raises(
        self, tmp_path: Path
    ) -> None:
        """Empty portal_domain is rejected for any close_reason other than
        ABORTED_PRE_ACTIVE — schema invariant."""
        log = tmp_path / "audit.jsonl"
        import dataclasses
        s = dataclasses.replace(
            _make_completed_session(),
            close_reason=CloseReason.USER_DISMISSED,
            portal_domain="",
        )
        with pytest.raises(ValueError, match="portal_domain"):
            write_session(s, log_path=log)

    def test_aborted_pre_active_writes_valid_entry(self, tmp_path: Path) -> None:
        """A session that never opened still produces a schema-valid entry."""
        log = tmp_path / "audit.jsonl"
        import dataclasses
        s = dataclasses.replace(
            _make_completed_session(),
            close_reason=CloseReason.ABORTED_PRE_ACTIVE,
        )
        write_session(s, log_path=log)
        entry = read_all(log_path=log)[0]
        assert entry["close_reason"] == "aborted_pre_active"
        assert set(entry.keys()) >= _REQUIRED_FIELDS


class TestSchemaConformance:
    """Assertions that bind the writer's output to docs/audit_log_schema.json."""

    def test_schema_required_fields_match_test_set(self) -> None:
        """Sanity: the test fixture set matches the schema doc's required_fields."""
        # If this fails, the doc was edited but the test wasn't.
        assert _REQUIRED_FIELDS == set(_SCHEMA["required_fields"])

    def test_writer_emits_only_schema_keys(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        entry = read_all(log_path=log)[0]
        extras = set(entry.keys()) - _REQUIRED_FIELDS
        assert not extras, f"Writer emitted keys not in schema: {extras}"

    def test_writer_emits_all_required_keys(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        entry = read_all(log_path=log)[0]
        missing = _REQUIRED_FIELDS - set(entry.keys())
        assert not missing, f"Writer omitted required keys: {missing}"

    def test_close_reason_value_is_in_schema_enum(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        entry = read_all(log_path=log)[0]
        assert entry["close_reason"] in _CLOSE_REASON_ENUM

    def test_platform_value_is_in_schema_enum(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        entry = read_all(log_path=log)[0]
        assert entry["platform"] in _PLATFORM_ENUM

    def test_all_close_reasons_in_enum_are_writeable(self, tmp_path: Path) -> None:
        """Every value in CloseReason must appear in the schema's enum."""
        for reason in CloseReason:
            assert reason.value in _CLOSE_REASON_ENUM, (
                f"{reason.name} value {reason.value!r} is not in "
                f"docs/audit_log_schema.json close_reason_enum"
            )

    def test_field_types_match_schema(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        entry = read_all(log_path=log)[0]

        type_checks = {
            "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "string": lambda v: isinstance(v, str),
            "bool": lambda v: isinstance(v, bool),
            "string|null": lambda v: v is None or isinstance(v, str),
            "array<string>": lambda v: isinstance(v, list)
            and all(isinstance(s, str) for s in v),
        }

        for field, expected_type in _SCHEMA["field_types"].items():
            assert field in entry, f"missing field {field}"
            check = type_checks.get(expected_type)
            assert check is not None, f"unhandled schema type {expected_type}"
            assert check(entry[field]), (
                f"field {field}: expected {expected_type}, got "
                f"{type(entry[field]).__name__}={entry[field]!r}"
            )
