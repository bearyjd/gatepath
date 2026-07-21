"""Adw.Application shell — GTK/PyGObject imports are guarded inside run_app().

This module is safe to *import* without PyGObject installed; only calling
run_app() will trigger gi imports.  This allows `python -m gatepath --help`
to work in environments without GTK.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from gatepath.portal_monitor import Monitor

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


def _start_portal_monitoring(
    win,
    probe_url: Optional[str],
    *,
    start: Optional[Callable[..., "Monitor"]] = None,
    launcher_factory: Optional[Callable[..., object]] = None,
) -> "Monitor":
    """Wire *win* to a polling portal monitor and start it.

    Builds a :class:`PortalLauncher` bridging the monitor's detection callback
    to ``win.open_portal`` (guarded by ``win.is_session_active``), then starts a
    polling :class:`Monitor` via ``start_nm_monitor``. Returns the started
    monitor; the caller must keep the returned handle alive so the daemon-thread
    poller (and, transitively, the launcher bound into its callback) is not
    garbage-collected.

    ``start`` and ``launcher_factory`` are injectable for headless unit tests;
    they default to the real ``start_nm_monitor`` / ``PortalLauncher``. This
    function is gi-free to import — GTK is only touched when the launcher
    dispatches to the main loop at detection time.
    """
    from gatepath.portal_launcher import PortalLauncher  # noqa: PLC0415
    from gatepath.portal_monitor import start_nm_monitor  # noqa: PLC0415

    start = start or start_nm_monitor
    launcher_factory = launcher_factory or PortalLauncher

    launcher = launcher_factory(
        open_portal=win.open_portal,
        is_session_active=win.is_session_active,
    )
    return start(launcher.on_detected, probe_url=probe_url)


def run_app(*, probe_url: Optional[str] = None) -> None:
    """Import gi, construct Adw.Application, and run the GTK main loop.

    Raises ImportError with a friendly message if PyGObject is not installed.
    """
    try:
        import gi  # noqa: PLC0415

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio  # noqa: PLC0415
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
            self._monitor: Optional[Monitor] = None

        def do_activate(self) -> None:  # type: ignore[override]
            win = self.get_active_window()
            if win is None:
                win = GatepathWindow(
                    application=self,
                    probe_url=self._probe_url,
                    isolation=isolation,
                    captive_interface_lookup=captive_lookup,
                )
                # Start the portal monitor exactly once, when the window is
                # first created — do_activate can fire repeatedly. The handle is
                # retained on the app so the daemon-thread poller isn't GC'd.
                if self._monitor is None:
                    self._monitor = _start_portal_monitoring(win, self._probe_url)
            win.present()

    app = GatepathApp()
    exit_code = app.run(None)
    logger.info("Application exited with code %d", exit_code)
