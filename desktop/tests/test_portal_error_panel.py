"""Construction tests for the portal load-error panel.

These need a real GTK 4 / libadwaita runtime and so are skipped where it is
absent — see the `gtk-widgets` CI job, which exists to make sure that skip
isn't the only outcome anywhere.

What they protect: the copy and retry policy are already covered headlessly in
`test_portal_load_error.py`, so the only thing left that can break is the
widget construction itself — a wrong `Adw` kwarg, a GTK3-ism, an escaping slip.
None of that is reachable without actually building the widget.
"""

from __future__ import annotations

import pytest

from gatepath.portal_load_error import PortalLoadError, PortalLoadErrorKind

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk  # noqa: F401
except ValueError:  # pragma: no cover - depends on host runtime
    pytest.skip("GTK 4 / libadwaita runtime not available", allow_module_level=True)

from gatepath.ui.portal_error_panel import build_error_panel  # noqa: E402


def _error(kind: PortalLoadErrorKind, host: str = "portal.airport.net") -> PortalLoadError:
    return PortalLoadError(kind=kind, host=host, technical_detail="WebKitNetworkError:300")


@pytest.mark.parametrize("kind", list(PortalLoadErrorKind))
def test_panel_builds_for_every_kind(kind: PortalLoadErrorKind) -> None:
    """Every kind must produce a widget — including ones not yet reachable.

    CERT_REJECTED has no code path feeding it until the TLS half of #114
    lands, so this is the only thing standing between it and a constructor
    bug that surfaces the day it does.
    """
    page = build_error_panel(_error(kind), on_retry=lambda: None)
    assert page is not None
    assert page.get_title()
    assert page.get_description()


def test_retryable_kind_gets_a_button() -> None:
    page = build_error_panel(_error(PortalLoadErrorKind.UNREACHABLE), on_retry=lambda: None)
    child = page.get_child()
    assert child is not None, "a retryable failure should offer a retry button"
    assert child.get_label() == "Try again"


def test_cert_rejection_gets_no_button() -> None:
    """The security-relevant case: no affordance to retry past a bad cert."""
    page = build_error_panel(_error(PortalLoadErrorKind.CERT_REJECTED), on_retry=lambda: None)
    assert page.get_child() is None


def test_no_button_when_no_retry_callback_supplied() -> None:
    page = build_error_panel(_error(PortalLoadErrorKind.UNREACHABLE), on_retry=None)
    assert page.get_child() is None


def test_retry_button_invokes_the_callback() -> None:
    calls: list[int] = []
    page = build_error_panel(
        _error(PortalLoadErrorKind.UNREACHABLE), on_retry=lambda: calls.append(1)
    )
    page.get_child().emit("clicked")
    assert calls == [1], "clicking Try again must reach the caller's retry handler"


def test_title_is_not_markup_escaped() -> None:
    """`Adw.StatusPage:title` is plain text, unlike `:description`.

    Escaping it renders a literal "Couldn&apos;t reach the sign-in page" on
    screen. Caught only by looking at the thing — construction succeeded
    either way — so it is pinned here now.
    """
    from gatepath import portal_load_error

    for kind in PortalLoadErrorKind:
        page = build_error_panel(_error(kind), on_retry=lambda: None)
        assert page.get_title() == portal_load_error.title(kind)
        assert "&apos;" not in page.get_title()
        assert "&amp;" not in page.get_title()


def test_icon_name_actually_resolves_in_the_icon_theme() -> None:
    """A non-existent icon name renders as a broken-image placeholder.

    `network-error-symbolic` sounds right and is absent from at least Breeze,
    which is how this was found. Construction can't catch it — GTK accepts any
    string — so resolve it against the live theme instead.
    """
    from gi.repository import Gdk, Gtk

    from gatepath.ui.portal_error_panel import ICON_NAME

    display = Gdk.Display.get_default()
    if display is None:  # pragma: no cover - no display in this environment
        pytest.skip("no display; icon theme unavailable")
    theme = Gtk.IconTheme.get_for_display(display)
    if not theme.has_icon("dialog-information-symbolic"):  # pragma: no cover
        pytest.skip("icon theme has no standard icons; nothing meaningful to assert")
    assert theme.has_icon(ICON_NAME), (
        f"{ICON_NAME!r} does not resolve in theme {theme.get_theme_name()!r}; "
        "it would render as a broken-image placeholder"
    )


def test_hostile_hostname_is_escaped_not_interpreted() -> None:
    """`host` comes off the network and Adw labels render Pango markup.

    Unescaped, a crafted hostname would break the panel or inject markup into
    it. The description must carry the escaped form.
    """
    page = build_error_panel(
        _error(PortalLoadErrorKind.UNREACHABLE, host="<b>evil</b>&co"),
        on_retry=lambda: None,
    )
    description = page.get_description()
    assert "<b>" not in description
    assert "&lt;b&gt;" in description
    assert "&amp;co" in description
