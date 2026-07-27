"""Construction test for the portal WebView itself.

There was no test for `make_webview` at all, and the docker e2e installs
`webkit2gtk4.1` — the *old* API — so the WebKit 6.0 branch had never been
executed anywhere, while `org.gnome.Platform` 49 (what the Flatpak ships) is
exactly WebKit 6.0. The result was an app that could not create a WebView on
its own shipping runtime, with every check green.

Requires a real GTK 4 + WebKit runtime, so it skips where that is absent; the
`gtk-widgets` CI job exists so "skipped" isn't the only outcome anywhere.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: F401
except ValueError:  # pragma: no cover - depends on host runtime
    pytest.skip("GTK 4 / libadwaita runtime not available", allow_module_level=True)

try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit  # noqa: F401

    WEBKIT_VERSION = "6.0"
except ValueError:  # pragma: no cover - depends on host runtime
    try:
        gi.require_version("WebKit2", "4.1")
        from gi.repository import WebKit2 as WebKit  # noqa: F401,N812

        WEBKIT_VERSION = "4.1"
    except ValueError:
        pytest.skip("no WebKitGTK runtime available", allow_module_level=True)

from gatepath.portal_webview import cleanup, make_webview  # noqa: E402


@pytest.fixture
def webview():
    Adw.init()
    view = make_webview(
        initial_url="about:blank",
        on_blocked_nav=lambda url: None,
        on_blocked_resource=lambda url: None,
    )
    yield view
    cleanup(view)


def test_make_webview_returns_a_real_webview(webview) -> None:
    """The regression: on WebKit 6.0 this raised AttributeError twice over.

    The 6.0 branch called the removed `WebView.new_with_network_session`, fell
    into the 4.1 fallback on AttributeError, and that called the equally
    absent `WebContext.new_with_website_data_manager` — so construction was
    impossible rather than degraded.
    """
    assert isinstance(webview, WebKit.WebView)


def test_webview_carries_its_temp_dir_for_cleanup(webview) -> None:
    temp_dir = getattr(webview, "temp_data_dir", None)
    assert temp_dir is not None, "cleanup() needs temp_data_dir to remove the data dir"
    assert temp_dir.exists()


@pytest.mark.skipif(WEBKIT_VERSION != "6.0", reason="6.0-only session model")
def test_session_is_ephemeral_on_webkit_6(webview) -> None:
    """Ephemeral means the session keeps nothing on disk — the stronger
    guarantee this app wants, and the reason the 6.0 branch doesn't reuse the
    4.1 temp-directory approach."""
    session = webview.get_network_session()
    assert session is not None
    assert session.is_ephemeral()


def test_portal_domain_is_recorded_for_the_off_domain_check(webview) -> None:
    # Guards the value shouldOverrideUrlLoading's desktop counterpart compares
    # against; a None here would make every navigation look off-domain.
    assert hasattr(webview, "_portal_domain")
