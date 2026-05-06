"""Pure-stdlib session controller — drives Active → Completed transitions and
writes the audit log. Owns the 10-minute timeout via a SessionTimer; the GTK
shell only provides a Scheduler implementation.

Splitting the controller from the GTK shell makes the timeout-end-to-end
contract testable in CI without a display server: tests inject a FakeScheduler
and call fire_all() to simulate 10 minutes elapsing.
"""

from __future__ import annotations

import dataclasses
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
from gatepath.session_timer import Scheduler, SessionTimer

logger = logging.getLogger(__name__)


class SessionController:
    """Owns the live PortalSession, the 10-minute timer, and audit writes.

    The controller is a single-session object: one Active session at a time.
    `set_active()` registers the live session AND arms the timeout. `close()`
    cancels the timer, transitions to Completed, writes the audit entry, and
    fires `on_close`.

    [scheduler] is required — production wiring passes a `GLibScheduler`,
    tests pass a `FakeScheduler` and use `scheduler.fire_all()` to simulate
    timer expiration. Without scheduler injection, the timer chain is
    untestable.

    [audit_log_path] is injectable so tests can point at a tmp file.
    [on_close] is invoked AFTER the audit entry is written.
    """

    def __init__(
        self,
        *,
        scheduler: Scheduler,
        audit_log_path: Optional[Path] = None,
        on_close: Optional[Callable[[PortalSession], None]] = None,
    ) -> None:
        self._session: Optional[PortalSession] = None
        self._audit_log_path = audit_log_path
        self._on_close = on_close
        self._timer = SessionTimer(scheduler)

    def set_active(self, session: PortalSession) -> None:
        """Register the live Active session and arm the 10-minute timer.

        Replaces any previous session and re-arms the timer (cancelling any
        prior schedule first).
        """
        if session.phase != PortalPhase.ACTIVE:
            raise ValueError(
                f"set_active requires phase=ACTIVE, got {session.phase!r}"
            )
        self._session = session
        self._timer.start(self.on_timeout)

    @property
    def session(self) -> Optional[PortalSession]:
        return self._session

    @property
    def is_timer_armed(self) -> bool:
        """True if the timeout is currently scheduled. Test introspection."""
        return self._timer.is_armed

    def close(self, reason: CloseReason) -> Optional[PortalSession]:
        """Transition to Completed with [reason], write the audit entry, fire
        on_close. Idempotent — safe to call after an already-closed session.

        Returns the final Completed session, or None if nothing to close.
        """
        # Always cancel the timer first — even if the close turns out to be a
        # no-op, we don't want a stale timer firing later.
        self._timer.cancel()

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
        current = self._session
        if current is None or current.phase != PortalPhase.ACTIVE:
            return
        self._session = dataclasses.replace(
            current,
            blocked_resource_requests=current.blocked_resource_requests + 1,
        )
