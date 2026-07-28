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
from gatepath.portal_load_error import PortalLoadError
from gatepath.portal_observations import collect_observations
from gatepath.portal_session import CloseReason, PortalPhase, PortalSession
from gatepath.session_controller import SessionController
from gatepath.ui.diagnosis_panel import DiagnosisPanel
from gatepath.ui.portal_error_panel import build_error_panel

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


def _begin_diagnostics_run(
    run_button: object,
    lookup: Optional[CaptiveInterfaceLookup],
    on_result: Callable[[DiagnosisResult], None],
    *,
    runner: Callable[..., None] = run_diagnostics_async,
) -> None:
    """Disable the run button and kick off an async diagnostics run.

    The interface label is resolved **on the worker thread** (via an
    ``interface_resolver`` closure), not here: ``resolve_interface_name`` can
    do blocking D-Bus round-trips through ``NMCaptiveInterfaceLookup``, and
    doing that on the GTK main loop would freeze the UI the instant the button
    is clicked — defeating the point of the async runner. Factored to module
    level (gi-free) so this control flow is unit-testable with a fake button
    and a fake runner, without a live display.
    """
    run_button.set_sensitive(False)
    runner(
        None,
        on_result,
        interface_resolver=lambda: resolve_interface_name(lookup),
    )


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
            # Guards against an in-flight diagnostics run's idle continuation
            # firing after the window is gone: a still-running worker will
            # GLib.idle_add(_on_diagnosis_result), which must not touch disposed
            # widgets. Cleared on close-request.
            self._alive = True
            self.set_title("Gatepath")
            self.set_default_size(900, 650)
            self._build_ui()
            self.connect("close-request", self._on_close_request)

        def _on_close_request(self, _window: object) -> bool:
            """Mark the window dead so a late diagnostics continuation no-ops.

            Returns False to let the default close proceed unchanged.
            """
            self._alive = False
            return False

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
            # Retained so the portal view can hand the window back on close.
            self._monitoring_content = toolbar_view
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

            Delegates to ``_begin_diagnostics_run`` (module level), which
            disables the button so a run can't be double-triggered and hands
            the battery to ``run_diagnostics_async`` — resolving the interface
            label on the worker thread, not here, so the (possibly blocking)
            D-Bus lookup never runs on the GTK main loop.
            """
            _begin_diagnostics_run(
                self._run_button,
                self._captive_interface_lookup,
                self._on_diagnosis_result,
            )

        def _on_diagnosis_result(self, result: DiagnosisResult) -> None:
            """Main-loop continuation once the battery finishes.

            Re-enables the run button, (re)renders the result into the panel,
            and drives the VPN banner from the *result* (not a second VPN
            call): reveal it when the top cause is VPN_BLOCKING, hide it
            otherwise.
            """
            if not self._alive:
                # The window was closed while this run was in flight; its
                # widgets may be disposed. Drop the stale continuation rather
                # than touch a finalized button/banner. (idle_add repetition is
                # already prevented by the runner's _deliver wrapper, which
                # returns False regardless of what this method returns.)
                return
            self._run_button.set_sensitive(True)
            self._ensure_diagnosis_panel().render(result)
            vpn_labels = vpn_labels_from_result(result)
            if vpn_labels:
                # Reuse the engine's recommended instruction as the banner text
                # so the VPN wording lives in one place (the engine), not a
                # second hand-authored copy here.
                self.show_vpn_warning(vpn_labels, result.recommended.instruction)
            else:
                self._vpn_banner.set_revealed(False)

        def show_vpn_warning(
            self, vpn_labels: list[str], instruction: Optional[str] = None
        ) -> None:
            """Reveal the in-app VPN warning banner.

            Uses *instruction* (the engine's recommended action text) when
            given, else a built-in fallback derived from *vpn_labels*. The
            banner title is Pango markup, so the message is escaped before it
            is set — ``vpn_labels`` are local interface names today, but
            escaping keeps this consistent with the panel and safe if the
            source ever widens.
            """
            logger.warning("VPN interfaces active: %s", vpn_labels)
            if instruction:
                message = instruction
            else:
                joined = ", ".join(vpn_labels) if vpn_labels else "unknown interface"
                message = (
                    f"A VPN ({joined}) may block captive sign-in. "
                    "Pause it, sign in, then re-enable."
                )
            self._vpn_banner.set_title(GLib.markup_escape_text(message))
            self._vpn_banner.set_revealed(True)

        def is_session_active(self) -> bool:
            """True iff the controller holds a live ACTIVE portal session.

            Public re-entrancy predicate for the portal launcher: it lets the
            launcher avoid opening a second portal while one is live without
            reaching into window/controller privates. This is a thin read over
            ``controller.session.phase`` — the isolated path also calls
            ``controller.set_active`` (see ``_try_open_portal_isolated``), so a
            portal opened via the netns helper reads as active here too.
            """
            session = self._controller.session
            return session is not None and session.phase == PortalPhase.ACTIVE

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
            # Unconfined in-process fallback. SECURITY_MODEL.md specifies this
            # for deployments without helper access — notably the Flatpak,
            # whose sandbox cannot reach the helper's system-bus name — and
            # accepts that WebKitGTK traffic is not route-confined there. The
            # VPN warning shown before this point is the documented mitigation.
            self._controller.set_active(active_session)
            self._open_portal_in_process(portal_url)

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
            # Remember the PID so we can collect what the WebView observed
            # once it exits — the counts live in that process (#123).
            self._portal_pid = result.pid
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
            self._collect_portal_observations()
            self._controller.close(close_reason)
            self._isolation.disengage()
            self.set_visible(True)
            return False

        def _collect_portal_observations(self) -> None:
            """Fold the exited subprocess's counts into the session.

            Must run BEFORE close(), which writes the audit entry. Failure here
            is survivable — the counters stay 0, exactly as they were before
            this channel existed — so it never blocks the close path.
            """
            pid = getattr(self, "_portal_pid", None)
            if pid is None:
                return
            observations = collect_observations(
                os.environ.get("XDG_RUNTIME_DIR"), pid
            )
            if observations is None:
                logger.info("no portal observations for pid=%s; counters stay 0", pid)
            else:
                logger.info(
                    "portal observations pid=%s: off-domain nav=%d, trackers=%d, "
                    "tls bypasses=%d",
                    pid,
                    observations.off_domain_navigations,
                    observations.tracker_resources,
                    observations.tls_cert_errors_bypassed,
                )
                self._controller.apply_observations(observations)
            self._portal_pid = None

        def _open_portal_in_process(self, portal_url: str) -> None:
            """Render the portal in this process and show it.

            The window previously had no portal view at all: this branch armed
            the 10-minute timer and left the monitoring page up, so a user
            whose deployment lacks helper access saw nothing happen and got a
            `timeout` audit entry ten minutes later.
            """
            try:
                from gatepath.portal_webview import make_webview  # noqa: PLC0415
            except ImportError as exc:
                logger.error("WebKitGTK unavailable; cannot show portal: %s", exc)
                self._show_portal_failure(
                    "Can't open the sign-in page",
                    "Gatepath needs WebKitGTK to display this network's sign-in "
                    "page, and it isn't available in this installation.",
                )
                return

            try:
                webview = make_webview(
                    initial_url=portal_url,
                    on_blocked_nav=lambda _url: self._controller.record_blocked_navigation(),
                    on_blocked_resource=lambda _url: self._controller.record_blocked_resource(),
                    on_load_error=self._on_portal_load_error,
                    on_tls_cert_bypassed=lambda _host: self._controller.record_tls_cert_bypassed(),
                )
            except Exception as exc:  # noqa: BLE001 — never leave the user on a blank window
                logger.exception("could not build the portal WebView: %s", exc)
                self._show_portal_failure(
                    "Can't open the sign-in page",
                    f"Gatepath couldn't start the browser view for this network. ({exc})",
                )
                return

            self._portal_webview = webview
            self._portal_url = portal_url
            self.set_content(self._build_portal_shell(webview))

        def _build_portal_shell(self, child: object) -> "Adw.ToolbarView":
            """Chrome around the portal: a header with the dismiss action."""
            shell = Adw.ToolbarView()
            header = Adw.HeaderBar()
            header.set_show_end_title_buttons(True)
            dismiss = Gtk.Button(label="Dismiss")
            dismiss.connect("clicked", lambda _b: self.dismiss_session())
            header.pack_start(dismiss)
            shell.add_top_bar(header)
            shell.set_content(child)  # type: ignore[arg-type]
            return shell

        def _on_portal_load_error(self, error: "PortalLoadError") -> None:
            """Show why the page didn't load instead of a blank WebView."""
            logger.warning(
                "portal load error: %s (%s)", error.kind.value, error.technical_detail
            )

            def _retry() -> None:
                webview = getattr(self, "_portal_webview", None)
                url = getattr(self, "_portal_url", None)
                if webview is None or url is None:
                    return
                self.set_content(self._build_portal_shell(webview))
                webview.load_uri(url)  # type: ignore[attr-defined]

            panel = build_error_panel(error, on_retry=_retry)
            self.set_content(self._build_portal_shell(panel))

        def _show_portal_failure(self, title: str, description: str) -> None:
            """Terminal failure before a WebView exists — say so, don't hang.

            Closes the session too: leaving it Active would run the 10-minute
            timer against a window that will never show a portal, which is the
            behaviour this whole path exists to remove.
            """
            page = Adw.StatusPage(
                icon_name="dialog-error-symbolic",
                title=GLib.markup_escape_text(title),
                description=GLib.markup_escape_text(description),
            )
            self.set_content(self._build_portal_shell(page))
            self._controller.close(CloseReason.ERROR)

        def _teardown_portal_view(self) -> None:
            """Drop the WebView and hand the window back to monitoring."""
            webview = getattr(self, "_portal_webview", None)
            if webview is not None:
                try:
                    from gatepath.portal_webview import cleanup  # noqa: PLC0415

                    cleanup(webview)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("portal WebView cleanup failed: %s", exc)
                self._portal_webview = None
                self._portal_url = None
            if getattr(self, "_monitoring_content", None) is not None:
                self.set_content(self._monitoring_content)

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
            self._teardown_portal_view()

except (ImportError, ValueError, AttributeError):
    # PyGObject not installed — define a stub so the module is importable
    # (though instantiation would fail).
    class GatepathWindow:  # type: ignore[no-redef]
        """Stub for environments without PyGObject."""

        def __init__(self, *args, **kwargs) -> None:  # type: ignore[misc]
            raise ImportError("PyGObject with GTK 4 is required for GatepathWindow.")

        def show_vpn_warning(
            self, vpn_labels: list[str], instruction: Optional[str] = None
        ) -> None:
            raise ImportError("PyGObject with GTK 4 is required for GatepathWindow.")

        def is_session_active(self) -> bool:
            raise ImportError("PyGObject with GTK 4 is required for GatepathWindow.")
