"""Pure-stdlib session timer with an injectable scheduler.

The 10-minute portal-session timeout is enforced by `SessionTimer`. It is
unit-testable without GTK because callers inject a `Scheduler` — the GTK shell
provides a `GLibScheduler` that wraps `GLib.timeout_add_seconds`; tests
provide a `FakeScheduler` that fires manually.

Splitting the timer from the GTK code is the only way to test this contract
in CI without a display server.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Protocol

from gatepath.portal_session import SESSION_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class Scheduler(Protocol):
    """Scheduler abstraction over `GLib.timeout_add_seconds` / similar."""

    def schedule(
        self,
        seconds: int,
        callback: Callable[[], None],
    ) -> object:
        """Schedule *callback* to fire once after *seconds*. Returns a handle."""

    def cancel(self, handle: object) -> None:
        """Cancel a previously-scheduled callback. No-op if already fired."""


class SessionTimer:
    """One-shot timer for the portal session timeout.

    Owns a single in-flight timer at a time. `start()` cancels any previous
    schedule before re-arming, so calling `start()` twice replaces the timer
    instead of doubling it.

    Thread-safety: NOT safe for concurrent `start()` / `cancel()` from multiple
    threads. The intended caller is the GTK main thread, which is single-
    threaded by design; concurrent calls would race on `_handle`. If used
    outside the GTK loop, wrap calls in an external lock.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        timeout_seconds: int = SESSION_TIMEOUT_SECONDS,
    ) -> None:
        self._scheduler = scheduler
        self._timeout_seconds = timeout_seconds
        self._handle: Optional[object] = None

    def start(self, on_timeout: Callable[[], None]) -> None:
        """Arm the timer. Cancels any previous schedule first."""
        self.cancel()
        self._handle = self._scheduler.schedule(
            self._timeout_seconds, on_timeout
        )
        logger.debug(
            "Session timer armed for %d seconds", self._timeout_seconds
        )

    def cancel(self) -> None:
        """Cancel the in-flight timer, if any."""
        if self._handle is not None:
            self._scheduler.cancel(self._handle)
            self._handle = None
            logger.debug("Session timer cancelled")

    @property
    def is_armed(self) -> bool:
        return self._handle is not None


class FakeScheduler:
    """Test-only scheduler. Records schedules and fires them manually."""

    def __init__(self) -> None:
        # List of (handle, seconds, callback) — handle is the index.
        self._scheduled: list[tuple[int, int, Callable[[], None]]] = []
        self._next_handle = 0

    def schedule(
        self,
        seconds: int,
        callback: Callable[[], None],
    ) -> object:
        handle = self._next_handle
        self._next_handle += 1
        self._scheduled.append((handle, seconds, callback))
        return handle

    def cancel(self, handle: object) -> None:
        self._scheduled = [s for s in self._scheduled if s[0] != handle]

    def fire_all(self) -> int:
        """Fire every scheduled callback. Returns the count fired."""
        scheduled = list(self._scheduled)
        self._scheduled.clear()
        for _, _, cb in scheduled:
            cb()
        return len(scheduled)

    @property
    def pending_count(self) -> int:
        return len(self._scheduled)
