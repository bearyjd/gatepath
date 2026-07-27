"""Pure model for "the desktop portal page did not load".

No `gi` / WebKit imports — same headless-safe contract as
`gatepath.ui.diagnosis_panel`'s pure layer, so the classification and the
user-facing copy are unit-testable without a GTK environment.

Counterpart to Android's `ui/PortalLoadError.kt`. The two apps share no code by
design, so this is a deliberate re-implementation, not an import.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

# WebKit GError domains, as the strings PyGObject exposes on `GLib.Error.domain`.
DOMAIN_NETWORK = "WebKitNetworkError"
DOMAIN_POLICY = "WebKitPolicyError"

# WebKitNetworkError codes.
_NETWORK_FAILED = 199
_NETWORK_TRANSPORT = 300
_NETWORK_UNKNOWN_PROTOCOL = 301
_NETWORK_CANCELLED = 302
_NETWORK_FILE_DOES_NOT_EXIST = 303

# WebKitPolicyError codes.
_POLICY_FRAME_LOAD_INTERRUPTED = 202


class PortalLoadErrorKind(enum.Enum):
    """Why the portal page failed to render."""

    #: Certificate could not be trusted and no bypass applied.
    CERT_REJECTED = "cert_rejected"
    #: Name resolution / transport failure reaching the gateway.
    UNREACHABLE = "unreachable"
    #: The gateway offered something the WebView can't display.
    UNSUPPORTED = "unsupported"
    #: Anything else, including WebKit's own generic failure.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PortalLoadError:
    """A load failure worth showing the user.

    `host` is a host, never a full URL: portal URLs routinely carry MAC
    addresses, gateway IPs and session tokens in query params, and this string
    reaches the screen.
    """

    kind: PortalLoadErrorKind
    host: str
    technical_detail: str


def is_deliberate_cancel(domain: str, code: int) -> bool:
    """True when *we* stopped the load, so no error should be shown.

    `_on_decide_policy` calls `decision.ignore()` for off-domain navigation.
    WebKit reports that as a load failure like any other — without this guard,
    every deliberately-refused navigation would pop an error panel at the user
    as though something had gone wrong.
    """
    if domain == DOMAIN_POLICY and code == _POLICY_FRAME_LOAD_INTERRUPTED:
        return True
    return domain == DOMAIN_NETWORK and code == _NETWORK_CANCELLED


def classify(domain: str, code: int) -> PortalLoadErrorKind:
    """Map a WebKit GError domain/code pair to a kind.

    Unrecognised domains and codes fall back to UNKNOWN rather than raising —
    a surprising error code must still produce a visible explanation, since
    the alternative is the silent failure this module exists to prevent.
    """
    if domain == DOMAIN_NETWORK:
        if code in (_NETWORK_TRANSPORT, _NETWORK_FAILED):
            return PortalLoadErrorKind.UNREACHABLE
        if code in (_NETWORK_UNKNOWN_PROTOCOL, _NETWORK_FILE_DOES_NOT_EXIST):
            return PortalLoadErrorKind.UNSUPPORTED
    if domain == DOMAIN_POLICY:
        return PortalLoadErrorKind.UNSUPPORTED
    return PortalLoadErrorKind.UNKNOWN


_TITLES = {
    PortalLoadErrorKind.CERT_REJECTED: "Sign-in page blocked for safety",
    PortalLoadErrorKind.UNREACHABLE: "Couldn't reach the sign-in page",
    PortalLoadErrorKind.UNSUPPORTED: "The sign-in page couldn't be displayed",
    PortalLoadErrorKind.UNKNOWN: "The sign-in page didn't load",
}


def title(kind: PortalLoadErrorKind) -> str:
    """Short headline for *kind*. Never empty."""
    return _TITLES[kind]


def body(error: PortalLoadError) -> str:
    """Explanation for *error*, naming the host when one is known.

    Points at the gateway rather than the user's machine — a captive gateway is
    the usual culprit and the copy shouldn't send people to debug their laptop.
    """
    where = error.host.strip() or "this network's sign-in page"
    if error.kind is PortalLoadErrorKind.CERT_REJECTED:
        return (
            f"{where} sent an invalid security certificate, so Gatepath "
            "refused to load it. This can mean the network is tampering with "
            "traffic. Avoid signing in here."
        )
    if error.kind is PortalLoadErrorKind.UNREACHABLE:
        return (
            f"Gatepath couldn't reach {where}. The network may still be "
            "setting up, or the gateway may be offline. Try again in a moment."
        )
    if error.kind is PortalLoadErrorKind.UNSUPPORTED:
        return (
            f"{where} returned something Gatepath can't display. This is "
            "usually a fault in the network's sign-in system."
        )
    return (
        f"Gatepath couldn't load {where} and the network didn't say why. "
        "Try again, or run diagnostics to look closer."
    )


def is_retryable(kind: PortalLoadErrorKind) -> bool:
    """Whether to offer a retry.

    Certificate rejections are not retryable: retrying re-rejects, and nudging
    the user past a possible tampering signal is the wrong affordance.
    """
    return kind is not PortalLoadErrorKind.CERT_REJECTED
