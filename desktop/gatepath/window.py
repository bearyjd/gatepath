"""AdwApplicationWindow — GTK imports are guarded; only imported from app.py.

This module should never be imported at the top level of any pure-stdlib
module.  It is imported lazily inside GatepathApp.do_activate().
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from gatepath.portal_session import PortalSession
from gatepath.session_controller import SessionController
from gatepath.session_timer import SessionTimer

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
        transitions and writes audit entries. The controller's `on_close`
        callback is wired to `_on_session_closed` here, which dismisses the
        WebView and switches back to the monitoring view.
        """

        def __init__(
            self,
            *,
            application: Adw.Application,
            probe_url: Optional[str] = None,
            session_controller: Optional[SessionController] = None,
        ) -> None:
            super().__init__(application=application)
            self._probe_url = probe_url
            # Default controller writes to the production audit log. Tests
            # should pass a controller pointed at a tmp file.
            self._controller = session_controller or SessionController(
                on_close=self._on_session_closed,
            )
            self._session_timer = SessionTimer(GLibScheduler())
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
            """Switch to the portal WebView and arm the session timeout.

            [active_session] must be in PortalPhase.ACTIVE — the caller
            (controller wiring in app.py) builds it via `to_active()` before
            handing it here.
            """
            logger.info("Opening portal: %s", portal_url)
            self._controller.set_active(active_session)
            self._session_timer.start(self._on_session_timeout)

        def _on_session_timeout(self) -> None:
            """Fired by the GLib scheduler 10 minutes after open_portal().

            Routes through the controller so the audit entry is written and
            the window is closed. No-op fallback removed — the contract is
            that the timer always materialises a TIMEOUT audit entry.
            """
            logger.warning("Session timed out — closing portal window")
            self._controller.on_timeout()

        def cancel_session_timer(self) -> None:
            """Cancel the timer when the user dismisses or the portal completes."""
            self._session_timer.cancel()

        def dismiss_session(self) -> None:
            """User-facing dismiss: cancel the timer, close via controller."""
            self._session_timer.cancel()
            self._controller.on_user_dismiss()

        def _on_session_closed(self, completed_session: PortalSession) -> None:
            """Controller callback after Completed transition + audit write.

            Default behaviour: cancel any in-flight timer and switch back to
            the monitoring view. Subclasses or wiring in app.py may override
            via the controller's `on_close` parameter.
            """
            self._session_timer.cancel()
            logger.info(
                "Session closed: reason=%s duration=%ss",
                completed_session.close_reason.value if completed_session.close_reason else "?",
                completed_session.duration_seconds,
            )

except (ImportError, ValueError):
    # PyGObject not installed — define a stub so the module is importable
    # (though instantiation would fail).
    class GatepathWindow:  # type: ignore[no-redef]
        """Stub for environments without PyGObject."""

        def __init__(self, *args, **kwargs) -> None:  # type: ignore[misc]
            raise ImportError("PyGObject with GTK 4 is required for GatepathWindow.")
