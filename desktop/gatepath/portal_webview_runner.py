"""Portal WebView runner subprocess (Phase 5c.2).

This module is invoked by the helper via `execv` AFTER it has setns'd into
the gatepath netns and dropped privilege to the calling user's UID. The
process inherits the netns via spawn, so all WebKit network requests go
through the captive interface — kernel-enforced, no leakage to the host
VPN regardless of what WebKit's network process decides to do.

The runner does NOT talk to the helper. Communication back to the parent
Gatepath process is via process exit code (and stderr captured by the
helper for the journal):

  - exit 0  → window closed cleanly (user-dismiss or sign-in complete)
  - exit 1  → portal URL failed to parse or load
  - exit 2  → required GTK/WebKit modules unavailable

Top-level imports stay stdlib only so the argv-validation / URL-parsing
surface is testable without GTK. The actual GTK + WebKit imports happen
inside :py:func:`run_window`.

Invocation contract: ``portal-webview-runner <portal_url>``.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def parse_argv(argv: list[str]) -> Optional[str]:
    """Validate and extract the portal URL from process argv.

    Mirrors the helper's URL gate: only ``http`` / ``https`` schemes are
    accepted, no control bytes, length-bounded. The helper validates
    before exec but we re-check in the spawned process so a future
    refactor can't accidentally widen the surface.

    Returns the URL string on success, ``None`` on validation failure.
    Caller is expected to exit non-zero when ``None`` is returned.
    """
    if len(argv) != 2:
        logger.error("expected exactly one argument (portal URL); got %d", len(argv) - 1)
        return None

    raw = argv[1]
    if len(raw) > 4096:
        logger.error("portal URL exceeds 4096 bytes")
        return None
    for byte in raw.encode("utf-8", errors="surrogateescape"):
        if byte < 0x20 or byte == 0x7F:
            logger.error("portal URL contains control byte 0x%02X", byte)
            return None

    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        logger.error("portal URL parse failed: %s", exc)
        return None

    if parsed.scheme not in {"http", "https"}:
        logger.error("portal URL scheme '%s' not allowed", parsed.scheme)
        return None
    if not parsed.netloc:
        logger.error("portal URL has no netloc")
        return None
    return raw


def run_window(portal_url: str) -> int:
    """Open the portal WebView and run the GTK main loop.

    Returns the exit code. Imports gi/GTK/WebKit inside the function so
    the module stays importable without PyGObject.

    Exit codes:
      - 0 — window closed cleanly
      - 1 — load failed
      - 2 — GTK or WebKit unavailable
    """
    try:
        import gi  # noqa: PLC0415

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: PLC0415
    except (ImportError, ValueError) as exc:
        logger.error("GTK/Adw unavailable: %s", exc)
        return 2

    try:
        from gatepath.portal_webview import make_webview  # noqa: PLC0415
    except ImportError as exc:
        logger.error("portal_webview unavailable: %s", exc)
        return 2

    blocked_count = {"nav": 0, "resource": 0}

    def on_blocked_nav(url: str) -> None:
        blocked_count["nav"] += 1
        logger.info("blocked off-domain nav: %s", url)

    def on_blocked_resource(url: str) -> None:
        blocked_count["resource"] += 1
        logger.info("blocked tracker resource: %s", url)

    try:
        webview = make_webview(
            initial_url=portal_url,
            on_blocked_nav=on_blocked_nav,
            on_blocked_resource=on_blocked_resource,
        )
    except ImportError as exc:
        logger.error("WebKit unavailable: %s", exc)
        return 2

    exit_code_holder = {"code": 0}

    class RunnerApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(application_id="com.ventouxlabs.Gatepath.PortalRunner")

        def do_activate(self) -> None:  # type: ignore[override]
            window = Adw.ApplicationWindow(application=self)
            window.set_title("Gatepath: Captive Portal Sign-in")
            window.set_default_size(900, 650)
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(Adw.HeaderBar())
            toolbar.set_content(webview)
            window.set_content(toolbar)
            window.connect("close-request", self._on_close)
            window.present()

        def _on_close(self, *_args: object) -> bool:
            # Returning False allows the window to close.
            return False

    app = RunnerApp()
    rc = app.run([])
    if rc != 0:
        logger.error("GTK app exited with rc=%d", rc)
        exit_code_holder["code"] = 1
    logger.info(
        "runner exiting code=%d (blocked nav=%d, resources=%d)",
        exit_code_holder["code"],
        blocked_count["nav"],
        blocked_count["resource"],
    )
    return exit_code_holder["code"]


def main(argv: list[str]) -> int:
    """Entry point. Returns the exit code instead of calling ``sys.exit``
    directly so tests can drive the parsing path without exiting pytest.
    """
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    portal_url = parse_argv(argv)
    if portal_url is None:
        return 1
    return run_window(portal_url)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
