"""Pure-stdlib session controller — drives Active → Completed transitions and
writes the audit log. The GTK shell uses this to implement the 10-minute
session timeout end-to-end (cancel timer, transition state, write audit entry,
ask the window to close).

Splitting the controller from the GTK shell makes the timeout-end-to-end
contract testable in CI without a display server.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from gatepath.audit_log import write_session
from gatepath.portal_session import (
    CloseReason,
    PortalPhase,
    PortalSession,
    to_completed,
)

logger = logging.getLogger(__name__)


class SessionController:
    """Owns the live PortalSession and writes audit entries on close.

    Designed for one session at a time. The GTK shell creates a fresh
    SessionController each time the portal window opens, calls `set_active()`
    when the WebView is ready, and routes user-dismiss / timeout / completed
    signals through the corresponding methods.

    [audit_log_path] is injectable so tests can point at a tmp file. The
    [on_close] callback is invoked AFTER the audit entry is written, so the
    GTK shell can use it to destroy the WebView / switch back to monitoring
    view.
    """

    def __init__(
        self,
        *,
        audit_log_path: Optional[Path] = None,
        on_close: Optional[Callable[[PortalSession], None]] = None,
    ) -> None:
        self._session: Optional[PortalSession] = None
        self._audit_log_path = audit_log_path
        self._on_close = on_close

    def set_active(self, session: PortalSession) -> None:
        """Register the live Active session. Replaces any previous session."""
        if session.phase != PortalPhase.ACTIVE:
            raise ValueError(
                f"set_active requires phase=ACTIVE, got {session.phase!r}"
            )
        self._session = session

    @property
    def session(self) -> Optional[PortalSession]:
        return self._session

    def close(self, reason: CloseReason) -> Optional[PortalSession]:
        """Transition to Completed with [reason], write the audit entry, fire
        on_close. No-op if no session is active or if the session is already
        terminal.

        Returns the final Completed session, or None if nothing to close.
        """
        current = self._session
        if current is None:
            logger.debug("close(%s) called with no active session", reason)
            return None
        if current.phase in (PortalPhase.COMPLETED, PortalPhase.ERROR):
            logger.debug("close(%s) called on already-terminal session", reason)
            return None

        completed = to_completed(
            current,
            reason=reason,
            blocked_nav=current.blocked_navigation_attempts,
            blocked_resources=current.blocked_resource_requests,
        )
        if completed is None:
            logger.warning(
                "to_completed returned None for phase=%s reason=%s",
                current.phase,
                reason,
            )
            return None

        try:
            write_session(completed, log_path=self._audit_log_path)
        except ValueError as exc:
            logger.error("Failed to write audit entry: %s", exc)
            # Continue anyway — closing the window is still important.

        self._session = completed
        if self._on_close is not None:
            try:
                self._on_close(completed)
            except Exception as exc:  # noqa: BLE001 — controller must not crash on UI errors
                logger.exception("on_close callback raised: %s", exc)
        return completed

    def on_timeout(self) -> Optional[PortalSession]:
        """Convenience: close with TIMEOUT. The 10-minute timer fires this."""
        return self.close(CloseReason.TIMEOUT)

    def on_user_dismiss(self) -> Optional[PortalSession]:
        return self.close(CloseReason.USER_DISMISSED)

    def on_portal_completed(self) -> Optional[PortalSession]:
        return self.close(CloseReason.PORTAL_COMPLETED)

    def record_blocked_navigation(self) -> None:
        """Increment the blocked-nav counter on the Active session."""
        import dataclasses

        current = self._session
        if current is None or current.phase != PortalPhase.ACTIVE:
            return
        self._session = dataclasses.replace(
            current,
            blocked_navigation_attempts=current.blocked_navigation_attempts + 1,
        )

    def record_blocked_resource(self) -> None:
        """Increment the observed-tracker counter on the Active session.
        See SECURITY_MODEL.md for why this is logged not blocked on desktop.
        """
        import dataclasses

        current = self._session
        if current is None or current.phase != PortalPhase.ACTIVE:
            return
        self._session = dataclasses.replace(
            current,
            blocked_resource_requests=current.blocked_resource_requests + 1,
        )
