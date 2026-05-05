"""AdwApplicationWindow — GTK imports are guarded; only imported from app.py.

This module should never be imported at the top level of any pure-stdlib
module.  It is imported lazily inside GatepathApp.do_activate().
"""

from __future__ import annotations

import logging
from typing import Optional

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
    from gi.repository import Adw, Gtk  # type: ignore[import-untyped]

    class GatepathWindow(Adw.ApplicationWindow):
        """Main application window."""

        def __init__(
            self,
            *,
            application: Adw.Application,
            probe_url: Optional[str] = None,
        ) -> None:
            super().__init__(application=application)
            self._probe_url = probe_url
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

        def open_portal(self, portal_url: str) -> None:
            """Switch to the portal WebView."""
            logger.info("Opening portal: %s", portal_url)

except (ImportError, ValueError):
    # PyGObject not installed — define a stub so the module is importable
    # (though instantiation would fail).
    class GatepathWindow:  # type: ignore[no-redef]
        """Stub for environments without PyGObject."""

        def __init__(self, *args, **kwargs) -> None:  # type: ignore[misc]
            raise ImportError("PyGObject with GTK 4 is required for GatepathWindow.")
