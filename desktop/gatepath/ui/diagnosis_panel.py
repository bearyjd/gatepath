"""Programmatic GTK4/libadwaita panel that renders a `DiagnosisResult`.

Built entirely in code — no ``.blp``/`Gtk.Builder`. The `gatepath/ui/*.blp`
files are vestigial (nothing in this codebase loads them: no `Gtk.Builder`,
no `Template`, no gresource in the build), and `window.py` constructs every
widget programmatically. This module follows that real pattern rather than
the design spec's letter, which named a `.blp` template.

Layering, mirroring `window.py`:

* The pure presentation helpers (`cause_label`, `report_detail`,
  `check_status`) live at module top level and never import ``gi`` — Task 5
  unit-tests them headless.
* The `DiagnosisPanel` widget lives inside a guarded ``try`` block; an
  importable stub is defined in the ``except`` tail so this module imports
  cleanly without PyGObject installed.

The panel never re-ranks: it renders `result.checks` in the exact order the
engine produced them (`engine._RANK` is the sole ranker). It also never
crashes on a report shape — every branch has a benign fallback.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from gatepath.diag.report import (
    Cause,
    ClockSkew,
    DiagnosticReport,
    DnsHijack,
    HttpProxyBlocking,
    HttpsOnlyCaptive,
    Inconclusive,
    NoDnsServers,
    PortalRedirectLoop,
    PrivateDnsBlocking,
    VpnBlocking,
)

if TYPE_CHECKING:  # pragma: no cover
    # Type-only: keeps the annotations honest without importing the engine
    # (or any I/O) at module load, matching diag/'s no-eager-import
    # discipline.
    from gatepath.diag.engine import DiagnosisResult, ProbeCheck

# ── Pure presentation helpers (no gi) ──────────────────────────────────

_CAUSE_LABELS: dict[Cause, str] = {
    Cause.HEALTHY: "No problem found",
    Cause.VPN_BLOCKING: "A VPN is blocking captive sign-in",
    Cause.DNS_HIJACK: "DNS answers are being intercepted",
    Cause.PRIVATE_DNS_BLOCKING: "Private DNS (DNS-over-TLS) is blocking sign-in",
    Cause.HTTP_PROXY_BLOCKING: "An HTTP proxy is eating the sign-in redirect",
    Cause.HTTPS_ONLY_CAPTIVE: "HTTPS is blocked or intercepted",
    Cause.NO_DNS_SERVERS: "This network offered no DNS servers",
    Cause.PORTAL_REDIRECT_LOOP: "The sign-in page is stuck in a redirect loop",
    Cause.CLOCK_SKEW: "The device clock is wrong",
    Cause.INCONCLUSIVE: "No clear cause found",
}

_UNKNOWN_CAUSE_LABEL = "Diagnosis unavailable"


def cause_label(cause: Cause) -> str:
    """Human-readable one-line label for a diagnosed cause.

    Degrades to a benign label for any value not in the known table rather
    than raising, so an unexpected cause never crashes the panel.
    """
    return _CAUSE_LABELS.get(cause, _UNKNOWN_CAUSE_LABEL)


def _tunnel_phrase(is_full_tunnel: bool) -> str:
    return "full-tunnel" if is_full_tunnel else "split-tunnel"


def report_detail(report: DiagnosticReport) -> str:
    """One-line, cause-specific detail string for a probe's report.

    Uses ``isinstance`` against the concrete report dataclasses so a report
    whose ``cause`` doesn't line up with its fields can never raise; any
    unmatched shape returns an empty string.
    """
    if isinstance(report, VpnBlocking):
        return (
            f"Interface {report.interface_name} "
            f"({_tunnel_phrase(report.is_full_tunnel)})"
        )
    if isinstance(report, DnsHijack):
        return (
            f"{report.host_probed}: system resolver says {report.system_answer}, "
            f"DoH says {report.doh_answer}"
        )
    if isinstance(report, PrivateDnsBlocking):
        if report.resolver_host:
            return f"Strict DNS-over-TLS via {report.resolver_host}"
        return "Strict DNS-over-TLS is active"
    if isinstance(report, HttpProxyBlocking):
        return report.description
    if isinstance(report, HttpsOnlyCaptive):
        return report.https_error_message
    if isinstance(report, NoDnsServers):
        return "DHCP handed this network zero DNS servers"
    if isinstance(report, PortalRedirectLoop):
        return f"Redirect chain revisits itself after {len(report.chain)} hops"
    if isinstance(report, ClockSkew):
        return f"Clock is off by about {report.skew_seconds // 60} minutes"
    if isinstance(report, Inconclusive):
        return "; ".join(report.probe_errors) or "No details reported"
    # Healthy, or any unknown/unmatched shape: nothing actionable to show.
    return ""


def _safe_markup(text: str) -> str:
    """Escape *text* for use as Adw row title/subtitle (Pango markup).

    Adw rows render titles/subtitles as Pango markup, so network-derived text
    (urllib error strings like ``<urlopen error ...>``, DoH answers) must be
    escaped or its literal ``<``/``>``/``&`` are malformed markup — a
    GLib-CRITICAL and a blank row on an *ordinary* HTTPS failure.

    ``html.escape(quote=False)`` escapes exactly ``&``, ``<``, ``>`` — the
    complete set for Pango *element* text (subtitles are element text, not
    attribute values, so ``'``/``"`` need no escaping). Kept as a pure,
    gi-free function so the escaping the panel depends on is unit-testable
    headless, without a GTK host — rather than calling ``GLib.markup_escape_text``
    only inside the widget, where CI (no GTK) never exercises it.
    """
    return html.escape(text, quote=False)


def check_status(cause: Cause) -> str:
    """Pass/fail/inconclusive verdict for a single check's cause.

    HEALTHY ⇒ ``"pass"``, INCONCLUSIVE ⇒ ``"inconclusive"``, anything
    else ⇒ ``"fail"``.
    """
    if cause is Cause.HEALTHY:
        return "pass"
    if cause is Cause.INCONCLUSIVE:
        return "inconclusive"
    return "fail"


_STATUS_LABELS: dict[str, str] = {
    "pass": "Pass",
    "fail": "Fail",
    "inconclusive": "Inconclusive",
}

_STATUS_CSS_CLASSES: dict[str, str] = {
    "pass": "success",
    "fail": "error",
    "inconclusive": "warning",
}


# ── GTK widget (guarded, mirroring window.py) ──────────────────────────

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk  # type: ignore[import-untyped]

    class DiagnosisPanel(Adw.Bin):
        """Renders a `DiagnosisResult` into a widget tree.

        A single reusable `Adw.Bin` whose child is (re)built on every
        `render()` call, so a repeated manual "Run diagnostics" (Task 3)
        just re-renders in place. The panel is pure presentation: it holds
        no diagnostic state and never mutates the result it is given.
        """

        def __init__(self) -> None:
            super().__init__()

        def render(self, result: "DiagnosisResult") -> None:
            """(Re)build the panel's children from ``result``.

            Never re-orders ``result.checks`` — the engine already ranked
            them. Never raises on an unexpected report shape.
            """
            container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
            container.set_margin_top(12)
            container.set_margin_bottom(12)
            container.set_margin_start(12)
            container.set_margin_end(12)

            container.append(self._build_headline(result))
            container.append(self._build_checks_group(result.checks))

            self.set_child(container)

        def _build_headline(self, result: "DiagnosisResult") -> "Gtk.Widget":
            top = result.top
            is_healthy = getattr(top, "cause", None) is Cause.HEALTHY

            group = Adw.PreferencesGroup()
            group.set_title("Most likely cause")

            row = Adw.ActionRow()
            row.set_title(cause_label(getattr(top, "cause", None)))
            row.set_title_lines(0)

            # A recommended action is only meaningful for an actual finding.
            # For a healthy result there is nothing to instruct, so the
            # subtitle stays empty even if a stray instruction were present.
            instruction = None if is_healthy else result.recommended.instruction
            if instruction:
                # Escape defensively (see _safe_markup): the instruction is
                # engine-authored constant text today, but a future one may
                # interpolate network-derived text, matching the check rows.
                row.set_subtitle(_safe_markup(instruction))
                row.set_subtitle_lines(0)

            group.add(row)
            return group

        def _build_checks_group(self, checks: "tuple[ProbeCheck, ...]") -> "Gtk.Widget":
            # No group title: the ExpanderRow below already carries "All checks",
            # so titling the group too would render the label twice.
            group = Adw.PreferencesGroup()

            expander = Adw.ExpanderRow()
            expander.set_title("All checks")
            expander.set_subtitle(
                f"{len(checks)} probe{'s' if len(checks) != 1 else ''} ran"
            )

            for check in checks:
                expander.add_row(self._build_check_row(check))

            group.add(expander)
            return group

        def _build_check_row(self, check: "ProbeCheck") -> "Gtk.Widget":
            cause = getattr(check.report, "cause", None)
            status = check_status(cause) if isinstance(cause, Cause) else "fail"

            row = Adw.ActionRow()
            row.set_title(check.probe_name)
            detail = report_detail(check.report)
            if detail:
                # Escape network-derived probe text before it hits the Pango
                # markup renderer (see _safe_markup).
                row.set_subtitle(_safe_markup(detail))
                row.set_subtitle_lines(0)

            badge = Gtk.Label(label=_STATUS_LABELS.get(status, status.title()))
            badge.add_css_class(_STATUS_CSS_CLASSES.get(status, "dim-label"))
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)
            return row

except (ImportError, ValueError, AttributeError):
    # PyGObject not installed — the pure helpers above still import; only the
    # widget is unavailable. Mirror window.py's importable-stub contract.
    class DiagnosisPanel:  # type: ignore[no-redef]
        """Stub for environments without PyGObject."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ImportError("PyGObject with GTK 4 is required for DiagnosisPanel.")

        def render(self, *args: object, **kwargs: object) -> None:
            raise ImportError("PyGObject with GTK 4 is required for DiagnosisPanel.")
