"""Adw.Application shell — GTK/PyGObject imports are guarded inside run_app().

This module is safe to *import* without PyGObject installed; only calling
run_app() will trigger gi imports.  This allows `python -m gatepath --help`
to work in environments without GTK.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


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
            "Install via: flatpak install cc.grepon.Gatepath\n"
            f"Original error: {exc}"
        ) from exc

    from gatepath.window import GatepathWindow  # noqa: PLC0415

    class GatepathApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id="cc.grepon.Gatepath",
                flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
            )
            self._probe_url = probe_url

        def do_activate(self) -> None:  # type: ignore[override]
            win = self.get_active_window()
            if win is None:
                win = GatepathWindow(
                    application=self,
                    probe_url=self._probe_url,
                )
            win.present()

    app = GatepathApp()
    exit_code = app.run(None)
    logger.info("Application exited with code %d", exit_code)
