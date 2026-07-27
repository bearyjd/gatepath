"""The "portal page didn't load" panel, as a constructible widget.

Split out of `portal_webview_runner.main()` so it can be built by a test.
While it lived inside `main()` nothing could reach it: a wrong `Adw` kwarg or
a GTK3-ism would have passed every check and failed only in front of a user
who was already looking at a broken sign-in.

Layering mirrors `diagnosis_panel`: the copy and the retry policy live in the
pure `gatepath.portal_load_error` module and are tested without a display;
this file only lays them out, behind the same gi guard `window.py` uses.
"""

from __future__ import annotations

from typing import Callable, Optional

from gatepath import portal_load_error
from gatepath.portal_load_error import PortalLoadError

# ── GTK widget (guarded, mirroring diagnosis_panel.py / window.py) ─────

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk  # type: ignore[import-untyped]

    def build_error_panel(
        error: PortalLoadError,
        on_retry: Optional[Callable[[], None]] = None,
    ) -> "Adw.StatusPage":
        """Build the panel shown in place of a portal page that failed to load.

        `on_retry` is omitted (and no button is drawn) when the failure isn't
        retryable — see `portal_load_error.is_retryable`. A certificate
        rejection is the case that matters: retrying re-rejects, and offering
        the button would nudge the user past a possible tampering signal.
        """
        # Adw labels render Pango markup, and `error.host` comes off the
        # network — escape it, or a crafted hostname breaks the panel or
        # injects markup into it.
        page = Adw.StatusPage(
            icon_name="network-error-symbolic",
            title=GLib.markup_escape_text(portal_load_error.title(error.kind)),
            description=GLib.markup_escape_text(portal_load_error.body(error)),
        )

        if on_retry is not None and portal_load_error.is_retryable(error.kind):
            retry = Gtk.Button(label="Try again")
            retry.set_halign(Gtk.Align.CENTER)
            retry.connect("clicked", lambda _button: on_retry())
            page.set_child(retry)

        return page

    GI_AVAILABLE = True

except (ImportError, ValueError):  # pragma: no cover - depends on host runtime
    GI_AVAILABLE = False

    def build_error_panel(  # type: ignore[misc]
        error: PortalLoadError,
        on_retry: Optional[Callable[[], None]] = None,
    ) -> object:
        """Stub used when no GTK 4 / libadwaita runtime is present.

        Raising keeps the failure loud and local. The module still imports
        cleanly without `gi`, which the headless-import contract requires.
        """
        raise RuntimeError(
            "GTK 4 / libadwaita runtime not available; cannot build the error panel"
        )
