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

logger = logging.getLogger(__name__)


def cleanup_temp_dir(path: Path) -> None:
    """Remove a temporary WebKit data directory.  Pure function — fully testable."""
    shutil.rmtree(path, ignore_errors=True)
    logger.debug("Cleaned up WebKit temp dir: %s", path)


def make_webview(
    initial_url: str,
    on_blocked_nav: Callable[[str], None],
    on_blocked_resource: Callable[[str], None],
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

    portal_domain = urlparse(initial_url).netloc

    # Dedicated ephemeral data directory per session.
    temp_dir = Path(tempfile.mkdtemp(prefix="gatepath-webkit-"))

    try:
        # WebKit 6.0 API
        network_session = WebKit.NetworkSession.new_ephemeral()
        data_manager = network_session.get_website_data_manager()
        webview = WebKit.WebView.new_with_network_session(network_session)
    except AttributeError:
        # WebKit2 4.1 fallback
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
        """Block navigations to off-portal domains."""
        try:
            NavigationType = WebKit.PolicyDecisionType
            if decision_type != NavigationType.NAVIGATION_ACTION:
                decision.use()
                return
            nav = decision.get_navigation_action()
            req = nav.get_request()
            nav_url = req.get_uri()
            nav_domain = urlparse(nav_url).netloc
            if nav_domain and nav_domain != portal_domain:
                logger.info("Blocking off-domain navigation to %s", nav_url)
                on_blocked_nav(nav_url)
                decision.ignore()
                return
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

    webview.connect("decide-policy", _on_decide_policy)
    webview.connect("resource-load-started", _on_resource_load)

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
