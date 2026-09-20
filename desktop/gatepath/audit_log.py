"""Audit log writer — pure stdlib, append-only JSONL.

Conforms exactly to docs/AUDIT_LOG_SCHEMA.md (schema_version=2).
Thread-safe via a module-level lock.  For tests, pass log_path explicitly
so no XDG directories are touched.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gatepath.portal_session import CloseReason, PortalSession

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

# schema_version 1 spelled the two counters `blocked_*`; v2 renamed them to
# `observed_*` (see docs/AUDIT_LOG_SCHEMA.md, "v1 -> v2"). Readers must map old
# lines so pre-rename sessions keep their counts. Names are duplicated from
# docs/audit_log_schema.json `v1_key_renames` because the schema file is not
# shipped in the Flatpak; tests/test_audit_log.py pins the two in sync, the
# same way AuditSchemaParityTest does for the Android reader.
_V1_KEY_RENAMES: dict[str, str] = {
    "blocked_navigation_attempts": "observed_navigation_attempts",
    "blocked_resource_requests": "observed_resource_requests",
}


def _default_log_path() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "gatepath" / "audit.jsonl"


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Format a datetime as ISO-8601 UTC with Z suffix, or None."""
    if dt is None:
        return None
    # Ensure UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_session(
    session: PortalSession,
    *,
    log_path: Optional[Path] = None,
) -> None:
    """Append one JSON line for *session* to the audit log.

    Raises ValueError if the session lacks the minimum fields needed
    for a valid log entry (portal_domain, session_opened_utc, close_reason).
    close_reason MUST be non-null; pre-Active aborts use
    CloseReason.ABORTED_PRE_ACTIVE — never None.

    `portal_domain` MAY be empty when close_reason == ABORTED_PRE_ACTIVE
    (a session terminated before any portal URL was observed, e.g. dismissal
    during MONITORING). For all other close reasons it must be non-empty.
    See docs/audit_log_schema.json `portal_domain_may_be_empty_when_close_reason_is`.
    """
    if session.close_reason is None:
        raise ValueError(
            "session.close_reason is required for audit log "
            "(use CloseReason.ABORTED_PRE_ACTIVE for sessions that never opened)"
        )
    if (
        session.close_reason != CloseReason.ABORTED_PRE_ACTIVE
        and not session.portal_domain
    ):
        raise ValueError(
            "session.portal_domain is required for audit log "
            "(empty allowed only for close_reason=aborted_pre_active)"
        )
    if session.session_opened_utc is None:
        raise ValueError("session.session_opened_utc is required for audit log")

    path = log_path or _default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    entry: dict = {
        "schema_version": 2,
        "timestamp_utc": _now_utc_iso(),
        "platform": "desktop",
        "ssid": session.ssid,
        "gateway_ip": session.gateway_ip,
        "portal_domain": session.portal_domain or "",
        "vpn_interfaces_detected": list(session.vpn_interfaces_detected),
        "vpn_warning_shown": session.vpn_warning_shown,
        "session_opened_utc": _utc_iso(session.session_opened_utc),
        "session_closed_utc": _utc_iso(session.session_closed_utc),
        "close_reason": session.close_reason.value,
        "duration_seconds": session.duration_seconds if session.duration_seconds is not None else 0,
        "observed_navigation_attempts": session.blocked_navigation_attempts,
        "observed_resource_requests": session.blocked_resource_requests,
        # Real count as of the observation channel (#123): the portal WebView
        # runs in a subprocess and its counters are folded into the session via
        # SessionController.apply_observations before this entry is written.
        # Still 0 when that file is missing or unreadable — a lost count must
        # not cost us the whole session record.
        "tls_cert_errors_bypassed": session.tls_cert_errors_bypassed,
        "confinement": session.confinement.value,
    }

    line = json.dumps(entry, ensure_ascii=False) + "\n"

    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    logger.debug("Audit entry written to %s", path)


def _upgrade_v1(entry: dict) -> dict:
    """Return *entry* with v1 counter keys renamed to their v2 spelling.

    Non-v1 lines come back unchanged. A new dict is built rather than the
    input mutated. This matches Android's `AuditLogWriter.upgradeV1` step
    exactly: `schema_version` stays 1 and no `confinement` field is added —
    the line is upgraded only as far as the schema's `v1_key_renames`
    contract asks. (Android then decodes into a typed entry whose v2-only
    fields carry defaults; this reader returns the raw dict, so a v1 line
    still lacks those keys — use `.get()` for them.)
    """
    if entry.get("schema_version") != 1:
        return entry
    return {_V1_KEY_RENAMES.get(key, key): value for key, value in entry.items()}


def read_all(*, log_path: Optional[Path] = None) -> list[dict]:
    """Return all audit entries in chronological (file) order.

    schema_version 1 lines are returned with their counters under the v2
    `observed_*` names (see `_upgrade_v1`), so callers only ever see one
    spelling.
    """
    path = log_path or _default_log_path()
    if not path.exists():
        return []
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning("Corrupt audit log line %d: %s", lineno, exc)
                continue
            if not isinstance(decoded, dict):
                logger.warning("Audit log line %d is not a JSON object; skipped", lineno)
                continue
            entries.append(_upgrade_v1(decoded))
    return entries
