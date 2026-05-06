"""Audit log writer — pure stdlib, append-only JSONL.

Conforms exactly to docs/AUDIT_LOG_SCHEMA.md (schema_version=1).
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
    """
    if not session.portal_domain:
        raise ValueError("session.portal_domain is required for audit log")
    if session.session_opened_utc is None:
        raise ValueError("session.session_opened_utc is required for audit log")
    if session.close_reason is None:
        raise ValueError(
            "session.close_reason is required for audit log "
            "(use CloseReason.ABORTED_PRE_ACTIVE for sessions that never opened)"
        )

    path = log_path or _default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    entry: dict = {
        "schema_version": 1,
        "timestamp_utc": _now_utc_iso(),
        "platform": "desktop",
        "ssid": session.ssid,
        "gateway_ip": session.gateway_ip,
        "portal_domain": session.portal_domain,
        "vpn_interfaces_detected": list(session.vpn_interfaces_detected),
        "vpn_warning_shown": session.vpn_warning_shown,
        "session_opened_utc": _utc_iso(session.session_opened_utc),
        "session_closed_utc": _utc_iso(session.session_closed_utc),
        "close_reason": session.close_reason.value,
        "duration_seconds": session.duration_seconds if session.duration_seconds is not None else 0,
        "blocked_navigation_attempts": session.blocked_navigation_attempts,
        "blocked_resource_requests": session.blocked_resource_requests,
    }

    line = json.dumps(entry, ensure_ascii=False) + "\n"

    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    logger.debug("Audit entry written to %s", path)


def read_all(*, log_path: Optional[Path] = None) -> list[dict]:
    """Return all audit entries in chronological (file) order."""
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
                entries.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                logger.warning("Corrupt audit log line %d: %s", lineno, exc)
    return entries
