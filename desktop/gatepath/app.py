"""Adw.Application shell — GTK/PyGObject imports are guarded inside run_app().

This module is safe to *import* without PyGObject installed; only calling
run_app() will trigger gi imports.  This allows `python -m gatepath --help`
to work in environments without GTK.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _try_build_isolation():
    """Probe the helper at startup. Returns
    ``(DesktopIsolation | None, CaptiveInterfaceLookup | None)``.

    On any failure (helper not installed, system bus unreachable, dasbus
    missing) returns ``(None, None)`` and logs a warning. The window
    receives ``None`` and falls back to the existing in-process WebView
    path — Flatpak-only deployments hit this path by design (no helper
    inside the sandbox).
    """
    from gatepath.netns_client import HelperUnavailable, NetnsClient  # noqa: PLC0415

    try:
        client = NetnsClient.connect()
    except HelperUnavailable as exc:
        logger.info("netns helper unavailable (%s); using in-process WebView", exc)
        return None, None

    try:
        from gatepath.desktop_isolation import (  # noqa: PLC0415
            DbusIsolationSignals,
            DesktopIsolation,
        )
        from gatepath.portal_monitor import NMCaptiveInterfaceLookup  # noqa: PLC0415

        signals = DbusIsolationSignals()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "could not construct isolation signals (%s); degrading to in-process WebView",
            exc,
        )
        return None, None

    isolation = DesktopIsolation(client, signals)
    lookup = NMCaptiveInterfaceLookup()
    logger.info("netns helper detected; isolation enabled")
    return isolation, lookup


def run_app(*, probe_url: Optional[str] = None) -> None:
    """Import gi, construct Adw.Application, and run the GTK main loop.

    Raises ImportError with a friendly message if PyGObject is not installed.
    """
    try:
        import gi  # noqa: PLC0415

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GLib  # noqa: PLC0415
    except (ImportError, ValueError) as exc:
        raise ImportError(
            "PyGObject with GTK 4 and libadwaita is required to run the Gatepath GUI.\n"
            "Install via: flatpak install com.ventouxlabs.Gatepath\n"
            f"Original error: {exc}"
        ) from exc

    from gatepath.window import GatepathWindow  # noqa: PLC0415

    isolation, captive_lookup = _try_build_isolation()

    class GatepathApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id="com.ventouxlabs.Gatepath",
                flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
            )
            self._probe_url = probe_url

        def do_activate(self) -> None:  # type: ignore[override]
            win = self.get_active_window()
            if win is None:
                win = GatepathWindow(
                    application=self,
                    probe_url=self._probe_url,
                    isolation=isolation,
                    captive_interface_lookup=captive_lookup,
                )
            win.present()

    app = GatepathApp()
    exit_code = app.run(None)
    logger.info("Application exited with code %d", exit_code)
