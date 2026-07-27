"""WebKit portal webview — GTK/WebKit imports are guarded inside make_webview().

Top-level imports: only stdlib + typing + blocked_domains helper.
This allows pure-stdlib tests to import this module and test the
cleanup_temp_dir() helper without a GTK environment.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from gatepath.blocked_domains import is_blocked
from gatepath.portal_load_error import (
    PortalLoadError,
    classify,
    is_deliberate_cancel,
)
from gatepath.webview_host_matching import is_same_origin_host

logger = logging.getLogger(__name__)


def cleanup_temp_dir(path: Path) -> None:
    """Remove a temporary WebKit data directory.  Pure function — fully testable."""
    shutil.rmtree(path, ignore_errors=True)
    logger.debug("Cleaned up WebKit temp dir: %s", path)


def make_webview(
    initial_url: str,
    on_blocked_nav: Callable[[str], None],
    on_blocked_resource: Callable[[str], None],
    on_load_error: Optional[Callable[[PortalLoadError], None]] = None,
) -> object:
    """Create and return a configured WebKitWebView.

    Imports gi/WebKit inside this function so the module stays importable
    without PyGObject.

    The returned object has a `.temp_data_dir` attribute (Path) so the
    caller can call cleanup() when the session ends.

    Args:
        initial_url: The captive portal URL to load first.
        on_blocked_nav: Called with the blocked URL when off-domain navigation
            is refused.
        on_blocked_resource: Called with the blocked URL when a sub-resource
            from a tracked domain is blocked.
        on_load_error: Called when a page load fails for a reason the user
            should see. When omitted, WebKit's own generic error page is left
            in place, which is the pre-existing behaviour.
    """
    try:
        import gi  # noqa: PLC0415

        try:
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit  # noqa: PLC0415
            _webkit_version = "6.0"
        except ValueError:
            gi.require_version("WebKit2", "4.1")
            from gi.repository import WebKit2 as WebKit  # noqa: PLC0415, N812
            _webkit_version = "4.1"
        logger.info("Using WebKit version %s", _webkit_version)
    except (ImportError, ValueError) as exc:
        raise ImportError(
            f"WebKitGTK is required for portal_webview.make_webview(): {exc}"
        ) from exc

    portal_domain = urlparse(initial_url).hostname or ""

    # Dedicated ephemeral data directory per session.
    temp_dir = Path(tempfile.mkdtemp(prefix="gatepath-webkit-"))

    # Branch on the version we actually resolved above, NOT on catching
    # AttributeError. The previous try/except conflated "this 6.0 call is
    # wrong" with "we are on 4.1": under WebKit 6.0 the first branch raised
    # AttributeError, fell into the 4.1 fallback, and that raised
    # AttributeError too — so no WebView could be created at all on the very
    # runtime the Flatpak ships (org.gnome.Platform 49 → WebKitGTK 6.0).
    if _webkit_version == "6.0":
        # 6.0 removed the webkit_web_view_new_with_* constructors; the session
        # is a construct-time GObject property instead. Ephemeral means the
        # session keeps nothing on disk at all, which is stronger than the
        # temp-directory approach the 4.1 path has to use.
        network_session = WebKit.NetworkSession.new_ephemeral()
        data_manager = network_session.get_website_data_manager()
        webview = WebKit.WebView(network_session=network_session)
    else:
        # WebKit2 4.1
        data_manager = WebKit.WebsiteDataManager(
            base_data_directory=str(temp_dir),
            base_cache_directory=str(temp_dir),
        )
        ctx = WebKit.WebContext.new_with_website_data_manager(data_manager)
        webview = WebKit.WebView.new_with_context(ctx)

    # Harden WebView settings.
    settings = webview.get_settings()
    try:
        settings.set_javascript_can_open_windows_automatically(False)
        settings.set_allow_modal_dialogs(False)
        settings.set_enable_java(False)
        settings.set_enable_plugins(False)
    except AttributeError:
        pass  # Some settings may not exist in all versions.

    # Store metadata on the webview object for cleanup.
    webview.temp_data_dir = temp_dir  # type: ignore[attr-defined]
    webview._portal_domain = portal_domain  # type: ignore[attr-defined]

    def _on_decide_policy(webview_obj, decision, decision_type):  # type: ignore[misc]
        """Observe off-domain navigations; let them load.

        Captive vendors (Meraki, Cisco ISE, UniFi, Aruba) POST the sign-in
        form to a backend on a DIFFERENT host than the splash page — splash on
        the AP's IP, grant POST to e.g. n143.network-auth.com. Refusing that
        navigation cancels the form submit, so the user presses Continue and
        nothing happens. Android hit exactly this and stopped refusing; the
        desktop kept refusing, which is what this restores parity on.

        The navigation is counted either way, so the audit log still records
        every off-domain hop. Protection against a hostile gateway steering
        the session somewhere else is certificate enforcement on the new host,
        not refusing to go there — refusing only breaks the legitimate case,
        since a hostile portal can redirect before we ever see a decision.
        """
        try:
            NavigationType = WebKit.PolicyDecisionType
            if decision_type != NavigationType.NAVIGATION_ACTION:
                decision.use()
                return
            nav = decision.get_navigation_action()
            req = nav.get_request()
            nav_url = req.get_uri()
            nav_host = urlparse(nav_url).hostname or ""
            if nav_host and not is_same_origin_host(nav_host, portal_domain):
                logger.info(
                    "Off-domain navigation to %s (portal host=%s) — allowing for captive flow",
                    nav_url,
                    portal_domain,
                )
                on_blocked_nav(nav_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Policy decision error: %s", exc)
        decision.use()

    def _on_resource_load(webview_obj, resource, request):  # type: ignore[misc]
        """Log blocked tracker resource loads."""
        try:
            res_url = request.get_uri()
            host = urlparse(res_url).netloc
            if is_blocked(host):
                logger.info("Blocked resource from %s: %s", host, res_url)
                on_blocked_resource(res_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Resource load check error: %s", exc)

    def _on_load_failed(webview_obj, load_event, failing_uri, error):  # type: ignore[misc]
        """Surface a load failure instead of leaving WebKit's generic page.

        Returns True only when we've handed the caller something to render;
        returning False lets WebKit fall back to its own error page, which is
        what happens when no on_load_error was supplied.
        """
        domain = str(getattr(error, "domain", "") or "")
        try:
            code = int(getattr(error, "code", 0) or 0)
        except (TypeError, ValueError):
            code = 0

        if is_deliberate_cancel(domain, code):
            # Cancelled, not failed — e.g. a redirect superseding an in-flight
            # load. Reporting it as a failure would blame the network for
            # something that did not go wrong.
            logger.debug("Load cancelled by policy: %s (%s:%d)", failing_uri, domain, code)
            return False

        logger.warning(
            "Portal load failed: %s (%s:%d) %s",
            urlparse(failing_uri).hostname or "(no host)",
            domain,
            code,
            getattr(error, "message", ""),
        )
        if on_load_error is None:
            return False

        on_load_error(
            PortalLoadError(
                kind=classify(domain, code),
                host=urlparse(failing_uri).hostname or "",
                technical_detail=f"{domain}:{code} {getattr(error, 'message', '')}".strip(),
            )
        )
        return True

    webview.connect("decide-policy", _on_decide_policy)
    webview.connect("resource-load-started", _on_resource_load)
    webview.connect("load-failed", _on_load_failed)

    # Load the initial portal URL.
    webview.load_uri(initial_url)

    return webview


def cleanup(webview: object) -> None:
    """Clear WebKit session data and remove the temp data directory."""
    temp_dir: Optional[Path] = getattr(webview, "temp_data_dir", None)

    # Best-effort clear of website data.
    try:
        import gi  # noqa: PLC0415

        try:
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit  # noqa: PLC0415

            ns = webview.get_network_session()  # type: ignore[attr-defined]
            dm = ns.get_website_data_manager()
            dm.clear(
                WebKit.WebsiteDataTypes.ALL,
                0,
                None,
                None,
                None,
            )
        except (AttributeError, ValueError):
            gi.require_version("WebKit2", "4.1")
            from gi.repository import WebKit2 as WebKit  # noqa: PLC0415, N812

            ctx = webview.get_context()  # type: ignore[attr-defined]
            dm = ctx.get_website_data_manager()
            dm.clear(WebKit.WebsiteDataTypes.ALL, 0, None, None, None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not clear WebKit data: %s", exc)

    if temp_dir is not None:
        cleanup_temp_dir(temp_dir)
