"""Portal monitor — detects captive portals via NetworkManager (dasbus) or polling.

Top-level imports: only stdlib + typing.
GTK / dasbus imports happen INSIDE the start_nm_monitor() function.

For environments without dasbus (or in tests), use the pure-Python
polling Monitor class which accepts an injectable probe_fn callable.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Polling interval when using the fallback poller (seconds).
_DEFAULT_POLL_INTERVAL = 30.0


class Monitor:
    """Polling-based captive-portal monitor.

    Calls probe_fn() on a daemon thread every poll_interval seconds.
    When probe_fn returns a portal URL, on_portal_detected is called
    on the same background thread.

    Designed to be fully unit-testable: inject fake probe_fn and
    on_portal_detected callbacks.
    """

    def __init__(
        self,
        probe_fn: Callable[[], Optional[str]],
        on_portal_detected: Callable[[str], None],
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._probe_fn = probe_fn
        self._on_portal_detected = on_portal_detected
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="gatepath-monitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("Portal monitor started (poll interval %.1fs)", self._poll_interval)

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval + 2)
        logger.info("Portal monitor stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                portal_url = self._probe_fn()
                if portal_url is not None:
                    logger.info("Portal detected via probe: %s", portal_url)
                    self._on_portal_detected(portal_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Monitor probe error: %s", exc)
            self._stop_event.wait(self._poll_interval)


def _make_probe_fn(probe_url: Optional[str] = None) -> Callable[[], Optional[str]]:
    """Return a probe function that wraps portal_probe.probe()."""
    from gatepath.portal_probe import CONNECTIVITY_CHECK_URL, probe  # noqa: PLC0415

    url = probe_url or CONNECTIVITY_CHECK_URL

    def _probe() -> Optional[str]:
        result = probe(url=url)
        if result.status == "portal":
            return result.portal_url
        return None

    return _probe


def start_nm_monitor(
    on_portal_detected: Callable[[str], None],
    probe_url: Optional[str] = None,
) -> Monitor:
    """Attempt to use NetworkManager via dasbus; fall back to polling Monitor.

    Returns the Monitor instance (already started).
    """
    try:
        import gi  # noqa: PLC0415

        gi.require_version("NM", "1.0")
        from gi.repository import NM  # noqa: PLC0415, F401

        # dasbus NM integration would be wired here.
        # For the MVP, we fall through to the polling fallback.
        logger.info("NetworkManager available; using polling fallback for MVP")
    except (ImportError, ValueError) as exc:
        logger.info("NetworkManager/dasbus unavailable (%s); using polling fallback", exc)

    probe_fn = _make_probe_fn(probe_url)
    monitor = Monitor(
        probe_fn=probe_fn,
        on_portal_detected=on_portal_detected,
    )
    monitor.start()
    return monitor
