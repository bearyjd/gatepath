"""Portal session state machine — pure stdlib, fully testable without GTK.

State transitions:
  IDLE -> MONITORING -> DETECTED -> ACTIVE -> COMPLETED | ERROR
  ACTIVE -> ERROR
  Any non-terminal -> ERROR

Only valid forward transitions are allowed; transition_or_none returns
None for illegal moves so callers can handle gracefully.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional


class PortalPhase(Enum):
    """Lifecycle phases of a captive-portal session."""

    IDLE = auto()
    MONITORING = auto()
    DETECTED = auto()
    ACTIVE = auto()
    COMPLETED = auto()
    ERROR = auto()


class CloseReason(str, Enum):
    """Why the portal session window was closed."""

    PORTAL_COMPLETED = "portal_completed"
    USER_DISMISSED = "user_dismissed"
    TIMEOUT = "timeout"
    ERROR = "error"


# Allowed (from_phase, to_phase) pairs.
_VALID_TRANSITIONS: frozenset[tuple[PortalPhase, PortalPhase]] = frozenset(
    {
        (PortalPhase.IDLE, PortalPhase.MONITORING),
        (PortalPhase.MONITORING, PortalPhase.DETECTED),
        (PortalPhase.MONITORING, PortalPhase.IDLE),
        (PortalPhase.DETECTED, PortalPhase.ACTIVE),
        (PortalPhase.DETECTED, PortalPhase.IDLE),
        (PortalPhase.ACTIVE, PortalPhase.COMPLETED),
        (PortalPhase.ACTIVE, PortalPhase.ERROR),
        # Any phase -> ERROR for unexpected failures.
        (PortalPhase.IDLE, PortalPhase.ERROR),
        (PortalPhase.MONITORING, PortalPhase.ERROR),
        (PortalPhase.DETECTED, PortalPhase.ERROR),
    }
)


@dataclasses.dataclass
class PortalSession:
    """Mutable session state.  Mutations use dataclasses.replace (no in-place edits)."""

    phase: PortalPhase = PortalPhase.IDLE

    # Network context (filled in at DETECTED phase).
    ssid: Optional[str] = None
    gateway_ip: Optional[str] = None
    portal_url: Optional[str] = None
    portal_domain: Optional[str] = None

    # VPN detection results (filled before ACTIVE).
    vpn_interfaces_detected: list[str] = dataclasses.field(default_factory=list)
    vpn_warning_shown: bool = False

    # Timing (filled at ACTIVE and COMPLETED phases).
    session_opened_utc: Optional[datetime] = None
    session_closed_utc: Optional[datetime] = None

    # Close metadata.
    close_reason: Optional[CloseReason] = None

    # Counters (incremented during ACTIVE via replace).
    blocked_navigation_attempts: int = 0
    blocked_resource_requests: int = 0

    @property
    def duration_seconds(self) -> Optional[int]:
        """Whole seconds between open and close, or None if not yet closed."""
        if self.session_opened_utc is None or self.session_closed_utc is None:
            return None
        delta = self.session_closed_utc - self.session_opened_utc
        return int(delta.total_seconds())

    def transition_or_none(
        self, target: PortalPhase
    ) -> Optional["PortalSession"]:
        """Return a new session advanced to *target*, or None if invalid."""
        if (self.phase, target) not in _VALID_TRANSITIONS:
            return None
        return dataclasses.replace(self, phase=target)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_detected(
    session: PortalSession,
    *,
    ssid: Optional[str],
    gateway_ip: Optional[str],
    portal_url: str,
    portal_domain: str,
    vpn_interfaces_detected: list[str],
    vpn_warning_shown: bool,
) -> Optional[PortalSession]:
    """Advance MONITORING -> DETECTED, attaching network context."""
    advanced = session.transition_or_none(PortalPhase.DETECTED)
    if advanced is None:
        return None
    return dataclasses.replace(
        advanced,
        ssid=ssid,
        gateway_ip=gateway_ip,
        portal_url=portal_url,
        portal_domain=portal_domain,
        vpn_interfaces_detected=list(vpn_interfaces_detected),
        vpn_warning_shown=vpn_warning_shown,
    )


def to_active(session: PortalSession) -> Optional[PortalSession]:
    """Advance DETECTED -> ACTIVE, recording the open timestamp."""
    advanced = session.transition_or_none(PortalPhase.ACTIVE)
    if advanced is None:
        return None
    return dataclasses.replace(advanced, session_opened_utc=_utcnow())


def to_completed(
    session: PortalSession,
    *,
    reason: CloseReason,
    blocked_nav: int,
    blocked_resources: int,
) -> Optional[PortalSession]:
    """Advance ACTIVE -> COMPLETED or ACTIVE -> ERROR, recording close metadata."""
    target = (
        PortalPhase.COMPLETED
        if reason != CloseReason.ERROR
        else PortalPhase.ERROR
    )
    advanced = session.transition_or_none(target)
    if advanced is None:
        return None
    return dataclasses.replace(
        advanced,
        close_reason=reason,
        session_closed_utc=_utcnow(),
        blocked_navigation_attempts=blocked_nav,
        blocked_resource_requests=blocked_resources,
    )
