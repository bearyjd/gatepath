"""Turns a monitor's ``on_portal_detected(portal_url)`` into a GTK-main-loop
``window.open_portal(portal_url, active_session)`` call.

The monitor fires its callback on a **daemon thread**; GTK work must happen on
the main loop. This module is the testable seam between the two, mirroring the
``diagnosis_runner`` pattern:

- It builds the ``PortalSession`` and gathers VPN context on the worker thread
  (``detect_vpn_interfaces`` does network I/O for Tailscale — that must stay off
  the main loop).
- It hands the actual GTK call to the main loop via ``dispatch``, which defaults
  to ``GLib.idle_add`` and is imported lazily *inside* the launcher so this
  module imports without PyGObject. Injecting ``dispatch`` (alongside the window
  method, the re-entrancy predicate, and the VPN detector) lets the whole seam
  be unit tested with no GTK and no network.

The re-entrancy guard is the security-relevant invariant: a portal that stays
detected across successive 30s polls must open **exactly once**. A second
``on_detected`` while a session is already live is dropped.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional
from urllib.parse import urlparse

from gatepath.portal_session import (
    PortalPhase,
    PortalSession,
    to_active,
    to_detected,
)
from gatepath.vpn_detector import detect_vpn_interfaces

logger = logging.getLogger(__name__)

# A callable that schedules a zero-arg ``callback()`` on the GTK main loop.
# ``GLib.idle_add(fn)`` has this shape (it repeats until ``fn`` returns False,
# which the scheduled callback does).
Dispatch = Callable[[Callable[[], object]], object]

# The window's portal opener: (portal_url, active_session) -> None.
OpenPortal = Callable[[str, PortalSession], None]


def build_detected_session(
    portal_url: str, *, vpn_labels: list[str]
) -> Optional[PortalSession]:
    """Advance a fresh session IDLE→MONITORING→DETECTED→ACTIVE.

    Pure: no GTK, no I/O. ``portal_domain`` is the ``netloc`` of *portal_url*
    (host, plus port if present), falling back to the parsed hostname when
    ``netloc`` is empty. ``vpn_interfaces_detected`` carries *vpn_labels*, and
    ``vpn_warning_shown`` reflects whether any VPN was detected.

    Returns the ACTIVE session, or ``None`` if any transition is rejected (which
    should not happen from a fresh session, but is handled defensively).
    """
    parsed = urlparse(portal_url)
    portal_domain = parsed.netloc or parsed.hostname or ""

    idle = PortalSession()
    monitoring = idle.transition_or_none(PortalPhase.MONITORING)
    if monitoring is None:
        logger.warning("portal_launcher: could not enter MONITORING from IDLE")
        return None

    detected = to_detected(
        monitoring,
        ssid=None,
        gateway_ip=None,
        portal_url=portal_url,
        portal_domain=portal_domain,
        vpn_interfaces_detected=vpn_labels,
        vpn_warning_shown=bool(vpn_labels),
    )
    if detected is None:
        logger.warning("portal_launcher: could not enter DETECTED from MONITORING")
        return None

    active = to_active(detected)
    if active is None:
        logger.warning("portal_launcher: could not enter ACTIVE from DETECTED")
        return None

    return active


class PortalLauncher:
    """Bridges the monitor's detection callback to the GTK window.

    Collaborators are injected so the launcher can be exercised headless:

    - *open_portal* — the window's ``open_portal(url, active_session)`` method.
    - *is_session_active* — predicate that is True while a portal session is
      already live; the re-entrancy guard consults it first.
    - *detect_vpn* — VPN-label gatherer (defaults to
      ``vpn_detector.detect_vpn_interfaces``); called on the worker thread.
    - *dispatch* — schedules a zero-arg callback on the GTK main loop; defaults
      to a lazily-imported ``GLib.idle_add`` so this module stays gi-free to
      import.
    """

    def __init__(
        self,
        open_portal: OpenPortal,
        is_session_active: Callable[[], bool],
        *,
        detect_vpn: Callable[[], list[str]] = detect_vpn_interfaces,
        dispatch: Dispatch | None = None,
    ) -> None:
        self._open_portal = open_portal
        self._is_session_active = is_session_active
        self._detect_vpn = detect_vpn
        self._dispatch = dispatch

    def _resolve_dispatch(self) -> Dispatch:
        if self._dispatch is not None:
            return self._dispatch
        from gi.repository import GLib  # Lazy: keeps the module gi-free to import.

        return GLib.idle_add

    def on_detected(self, portal_url: str) -> None:
        """Monitor callback (runs on the monitor's daemon thread).

        Never raises: the monitor loop logs and continues, but this is defensive
        regardless. Applies the re-entrancy guard first, gathers VPN context,
        builds the ACTIVE session, then schedules ``open_portal`` on the main
        loop.
        """
        try:
            if self._is_session_active():
                logger.info(
                    "portal_launcher: portal already active; ignoring re-detection"
                )
                return

            try:
                vpn_labels = self._detect_vpn()
            except Exception:  # noqa: BLE001 — VPN detection is best-effort.
                logger.warning(
                    "portal_launcher: VPN detection failed; proceeding with none",
                    exc_info=True,
                )
                vpn_labels = []

            session = build_detected_session(portal_url, vpn_labels=vpn_labels)
            if session is None:
                logger.warning(
                    "portal_launcher: could not build session for %s", portal_url
                )
                return

            dispatch = self._resolve_dispatch()

            def _open_on_main_loop() -> bool:
                # Returns False so GLib.idle_add runs it exactly once.
                self._open_portal(portal_url, session)
                return False

            dispatch(_open_on_main_loop)
        except Exception:  # noqa: BLE001 — must not escape the monitor thread.
            logger.exception("portal_launcher: on_detected failed for %s", portal_url)
