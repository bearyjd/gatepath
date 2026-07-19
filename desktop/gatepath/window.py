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
from gatepath.diag.engine import DiagnosisResult
from gatepath.diag.report import Cause
from gatepath.diagnosis_runner import run_diagnostics_async
from gatepath.portal_monitor import CaptiveInterfaceLookup
from gatepath.portal_session import CloseReason, PortalSession
from gatepath.session_controller import SessionController
from gatepath.ui.diagnosis_panel import DiagnosisPanel

logger = logging.getLogger(__name__)

# Cosmetic label used when no captive interface is resolvable. Every desktop
# probe uses the system default route (unbound sockets); ``interface_name`` is
# a display label that lands in the context and ``VpnBlocking.interface_name``,
# never a bind target — so a stable placeholder is correct here.
_DEFAULT_ROUTE_LABEL = "(default route)"


def resolve_interface_name(lookup: Optional[CaptiveInterfaceLookup]) -> str:
    """Best-effort interface *label* for a manual diagnostics run.

    Prefers the captive-interface lookup when the window was built with one and
    it yields a non-empty name; otherwise falls back to a stable placeholder.
    Pure (no ``gi``, no I/O beyond the lookup) so it is unit-testable headless.
    """
    if lookup is not None:
        name = lookup.get_captive_interface()
        if name:
            return name
    return _DEFAULT_ROUTE_LABEL


def vpn_labels_from_result(result: DiagnosisResult) -> list[str]:
    """VPN interface label(s) to surface in the banner, or ``[]``.

    Driven off the diagnosis result's *top* finding (not a second independent
    VPN call): a non-empty list is returned only when the top cause is
    ``VPN_BLOCKING``, in which case it carries that report's interface name.
    Pure, so the banner decision is unit-testable without a live display.
    """
    top = result.top
    if getattr(top, "cause", None) is Cause.VPN_BLOCKING:
        return [top.interface_name]
    return []


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
            # Diagnosis UI: the panel is created lazily on the first manual
            # run and re-rendered in place on every subsequent run. The banner
            # and button are built eagerly in _build_ui.
            self._diagnosis_panel: Optional[DiagnosisPanel] = None
            self.set_title("Gatepath")
            self.set_default_size(900, 650)
            self._build_ui()

        def _build_ui(self) -> None:
            """Construct the initial monitoring UI."""
            toolbar_view = Adw.ToolbarView()
            header = Adw.HeaderBar()
            toolbar_view.add_top_bar(header)

            # VPN warning banner: built hidden, revealed only when a diagnosis
            # result's top cause is VPN_BLOCKING (see _on_diagnosis_result).
            self._vpn_banner = Adw.Banner()
            self._vpn_banner.set_revealed(False)
            toolbar_view.add_top_bar(self._vpn_banner)

            # Vertical content: the monitoring status page on top, and the
            # diagnosis panel appended below it on the first manual run.
            content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

            status_page = Adw.StatusPage()
            status_page.set_title("Monitoring for Captive Portal")
            status_page.set_description(
                "Gatepath will open a secure window when a captive portal is detected.\n\n"
                "Note: If a full-tunnel VPN is active, the portal page may not load.\n"
                "Consider pausing your VPN before connecting to this network."
            )
            status_page.set_icon_name("network-wireless-symbolic")
            # Let the panel below claim vertical space once it appears.
            status_page.set_vexpand(False)

            # "Run diagnostics" is always available on the monitoring view.
            self._run_button = Gtk.Button(label="Run diagnostics")
            self._run_button.add_css_class("pill")
            self._run_button.add_css_class("suggested-action")
            self._run_button.set_halign(Gtk.Align.CENTER)
            self._run_button.connect("clicked", self._on_run_diagnostics_clicked)
            status_page.set_child(self._run_button)

            content_box.append(status_page)
            self._content_box = content_box

            toolbar_view.set_content(content_box)
            self.set_content(toolbar_view)

        def _ensure_diagnosis_panel(self) -> DiagnosisPanel:
            """Create the diagnosis panel on first use, appended below the
            status page inside a scroller; return the existing one thereafter.
            """
            if self._diagnosis_panel is None:
                panel = DiagnosisPanel()
                scroller = Gtk.ScrolledWindow()
                scroller.set_vexpand(True)
                scroller.set_child(panel)
                self._content_box.append(scroller)
                self._diagnosis_panel = panel
            return self._diagnosis_panel

        def _on_run_diagnostics_clicked(self, _button: "Gtk.Button") -> None:
            """Kick off a manual diagnostics run off the main loop.

            Disables the button so a run can't be double-triggered, resolves a
            best-effort interface label, and hands the battery to
            ``run_diagnostics_async`` (which offloads it to a worker thread and
            bounces the result back via ``GLib.idle_add``).
            """
            self._run_button.set_sensitive(False)
            interface_name = resolve_interface_name(self._captive_interface_lookup)
            logger.info("Running diagnostics for interface label %r", interface_name)
            run_diagnostics_async(interface_name, self._on_diagnosis_result)

        def _on_diagnosis_result(self, result: DiagnosisResult) -> None:
            """Main-loop continuation once the battery finishes.

            Re-enables the run button, (re)renders the result into the panel,
            and drives the VPN banner from the *result* (not a second VPN
            call): reveal it when the top cause is VPN_BLOCKING, hide it
            otherwise.
            """
            self._run_button.set_sensitive(True)
            self._ensure_diagnosis_panel().render(result)
            vpn_labels = vpn_labels_from_result(result)
            if vpn_labels:
                self.show_vpn_warning(vpn_labels)
            else:
                self._vpn_banner.set_revealed(False)

        def show_vpn_warning(self, vpn_labels: list[str]) -> None:
            """Reveal the in-app VPN warning banner with the given label(s)."""
            logger.warning("VPN interfaces active: %s", vpn_labels)
            joined = ", ".join(vpn_labels) if vpn_labels else "unknown interface"
            self._vpn_banner.set_title(
                f"A VPN ({joined}) may block captive sign-in. "
                "Pause it, sign in, then re-enable."
            )
            self._vpn_banner.set_revealed(True)

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

        def show_vpn_warning(self, vpn_labels: list[str]) -> None:
            raise ImportError("PyGObject with GTK 4 is required for GatepathWindow.")
