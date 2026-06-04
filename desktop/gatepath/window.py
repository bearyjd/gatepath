"""AdwApplicationWindow — GTK imports are guarded; only imported from app.py.

This module should never be imported at the top level of any pure-stdlib
module.  It is imported lazily inside GatepathApp.do_activate().
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

from gatepath.desktop_isolation import (
    DesktopIsolation,
    EngageRefused,
    EngageSuccess,
    wait_result_to_close_reason,
)
from gatepath.portal_monitor import CaptiveInterfaceLookup
from gatepath.portal_session import CloseReason, PortalSession
from gatepath.session_controller import SessionController

logger = logging.getLogger(__name__)


def _require_gtk() -> None:
    """Ensure GTK 4 + Adw are available; raise ImportError otherwise."""
    import gi  # noqa: PLC0415

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")


# Actual class body — only evaluated when this module is imported, which
# only happens after run_app() has already loaded gi.
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk  # type: ignore[import-untyped]

    class GLibScheduler:
        """`Scheduler` implementation backed by `GLib.timeout_add_seconds`.

        Returned handles are GLib source IDs (positive ints). `cancel()` calls
        `GLib.source_remove`, which is a no-op if the source already fired.
        """

        def schedule(
            self,
            seconds: int,
            callback: Callable[[], None],
        ) -> object:
            def _wrapped() -> bool:
                callback()
                return GLib.SOURCE_REMOVE

            return GLib.timeout_add_seconds(seconds, _wrapped)

        def cancel(self, handle: object) -> None:
            if isinstance(handle, int):
                GLib.source_remove(handle)

    class GatepathWindow(Adw.ApplicationWindow):
        """Main application window.

        The window owns a [SessionController] that drives Active → Completed
        transitions, owns the 10-minute timer, and writes audit entries. The
        controller's `on_close` callback is wired here so the window can
        dismiss the WebView and switch back to the monitoring view.
        """

        def __init__(
            self,
            *,
            application: Adw.Application,
            probe_url: Optional[str] = None,
            session_controller: Optional[SessionController] = None,
            isolation: Optional[DesktopIsolation] = None,
            captive_interface_lookup: Optional[CaptiveInterfaceLookup] = None,
        ) -> None:
            super().__init__(application=application)
            self._probe_url = probe_url
            # Default controller writes to the production audit log and uses
            # GLib for its timer. Tests inject their own controller with a
            # FakeScheduler.
            self._controller = session_controller or SessionController(
                scheduler=GLibScheduler(),
                on_close=self._on_session_closed,
            )
            # Phase 5c.3: helper-driven isolation. Both must be present
            # for the isolated path to engage; either ``None`` keeps the
            # window on the existing in-process WebView path (matches the
            # plan's degradation contract for Flatpak-only deployments).
            self._isolation = isolation
            self._captive_interface_lookup = captive_interface_lookup
            self.set_title("Gatepath")
            self.set_default_size(900, 650)
            self._build_ui()

        def _build_ui(self) -> None:
            """Construct the initial monitoring UI."""
            toolbar_view = Adw.ToolbarView()
            header = Adw.HeaderBar()
            toolbar_view.add_top_bar(header)

            status_page = Adw.StatusPage()
            status_page.set_title("Monitoring for Captive Portal")
            status_page.set_description(
                "Gatepath will open a secure window when a captive portal is detected.\n\n"
                "Note: If a full-tunnel VPN is active, the portal page may not load.\n"
                "Consider pausing your VPN before connecting to this network."
            )
            status_page.set_icon_name("network-wireless-symbolic")

            toolbar_view.set_content(status_page)
            self.set_content(toolbar_view)

        def show_vpn_warning(self, vpn_labels: list[str]) -> None:
            """Show an in-app VPN warning banner."""
            logger.warning("VPN interfaces active: %s", vpn_labels)

        def open_portal(self, portal_url: str, active_session: PortalSession) -> None:
            """Switch to the portal WebView via the controller.

            [active_session] must be in PortalPhase.ACTIVE — the caller
            (controller wiring in app.py) builds it via `to_active()` before
            handing it here. The controller arms its own 10-minute timer.

            Phase 5c.3: when both ``isolation`` and ``captive_interface_lookup``
            were supplied at construction time AND the lookup returns an
            interface, route through the helper-driven netns subprocess
            instead of the in-process WebView. On engage refusal we
            degrade to the in-process path (the existing default).
            """
            logger.info("Opening portal: %s", portal_url)
            if self._try_open_portal_isolated(portal_url, active_session):
                return
            self._controller.set_active(active_session)

        def _try_open_portal_isolated(
            self, portal_url: str, active_session: PortalSession
        ) -> bool:
            """Attempt the isolated path. Returns True iff the helper
            engaged and the worker thread is now waiting for the
            subprocess to exit. Returns False if isolation isn't
            configured or the helper refused — caller should fall back
            to the in-process path.
            """
            if self._isolation is None or self._captive_interface_lookup is None:
                return False
            interface = self._captive_interface_lookup.get_captive_interface()
            if interface is None:
                logger.info(
                    "isolation configured but no captive interface visible; "
                    "using in-process WebView"
                )
                return False
            # DESK-004: the WebView runs in its own netns-joined transient unit
            # with no inherited environment, so forward this UI process's
            # graphical-session identifiers. This is the one place that reads the
            # display env; the helper validates them and derives the rest from
            # the authenticated caller UID.
            result = self._isolation.engage(
                portal_url,
                interface,
                wayland_display=os.environ.get("WAYLAND_DISPLAY", ""),
                x_display=os.environ.get("DISPLAY", ""),
                x_authority=os.environ.get("XAUTHORITY", ""),
            )
            if isinstance(result, EngageRefused):
                logger.info(
                    "helper engage refused (stage=%s, reason=%s); "
                    "using in-process WebView",
                    result.stage,
                    result.reason,
                )
                # If the refusal happened at the launch stage, the helper
                # has the netns active — disengage so we don't leak it
                # past this call.
                if result.stage == "launch":
                    self._isolation.disengage()
                return False
            assert isinstance(result, EngageSuccess)
            logger.info(
                "helper engaged: pid=%d netns=%s", result.pid, result.netns_path
            )
            self._controller.set_active(active_session)
            self.set_visible(False)
            threading.Thread(
                target=self._wait_for_subprocess_thread,
                name="gatepath-isolation-wait",
                daemon=True,
            ).start()
            return True

        def _wait_for_subprocess_thread(self) -> None:
            """Worker-thread body: blocks on the helper's exit signal,
            then bounces back to the GTK thread to close the session.
            """
            assert self._isolation is not None
            wait_result = self._isolation.wait_for_subprocess()
            close_reason = wait_result_to_close_reason(wait_result)
            GLib.idle_add(self._on_subprocess_done, close_reason)

        def _on_subprocess_done(self, close_reason: CloseReason) -> bool:
            """GTK-thread continuation after the subprocess exits.

            Returning False so GLib.idle_add doesn't repeat us.
            """
            logger.info("portal subprocess exited (close_reason=%s)", close_reason)
            assert self._isolation is not None
            self._controller.close(close_reason)
            self._isolation.disengage()
            self.set_visible(True)
            return False

        def dismiss_session(self) -> None:
            """User-facing dismiss: route through controller (cancels timer + writes audit)."""
            self._controller.on_user_dismiss()

        def _on_session_closed(self, completed_session: PortalSession) -> None:
            """Controller callback after Completed transition + audit write.

            Switch back to the monitoring view. The controller has already
            cancelled its timer and written the audit entry.
            """
            logger.info(
                "Session closed: reason=%s duration=%ss",
                completed_session.close_reason.value if completed_session.close_reason else "?",
                completed_session.duration_seconds,
            )

except (ImportError, ValueError, AttributeError):
    # PyGObject not installed — define a stub so the module is importable
    # (though instantiation would fail).
    class GatepathWindow:  # type: ignore[no-redef]
        """Stub for environments without PyGObject."""

        def __init__(self, *args, **kwargs) -> None:  # type: ignore[misc]
            raise ImportError("PyGObject with GTK 4 is required for GatepathWindow.")
