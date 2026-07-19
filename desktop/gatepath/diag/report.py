"""Diagnostic cause vocabulary for the desktop engine.

Mirror of the Android sealed `DiagnosticReport` hierarchy
(`android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticReport.kt`).
The [Cause] values are spelled exactly as the Kotlin variant names because
PR 5's cross-platform parity guard string-matches them — treat the spelling
as a wire contract, not a label.

Desktop legitimately lacks three Android causes: `PrivateDnsBlocking`
(Android system Private DNS), `CellularFallback` (no cellular), and
`SandboxedWebView` (Android WebView process model). The parity guard
encodes that allowlist.

Pure module: no I/O, no platform imports.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import ClassVar, Optional, Union


class Cause(str, enum.Enum):
    """One diagnosed cause. Values mirror the Kotlin variant names."""

    HEALTHY = "Healthy"
    VPN_BLOCKING = "VpnBlocking"
    DNS_HIJACK = "DnsHijack"
    HTTP_PROXY_BLOCKING = "HttpProxyBlocking"
    HTTPS_ONLY_CAPTIVE = "HttpsOnlyCaptive"
    NO_DNS_SERVERS = "NoDnsServers"
    PORTAL_REDIRECT_LOOP = "PortalRedirectLoop"
    CLOCK_SKEW = "ClockSkew"
    INCONCLUSIVE = "Inconclusive"


@dataclasses.dataclass(frozen=True)
class Healthy:
    """Probe ran cleanly and saw no problem on its dimension."""

    cause: ClassVar[Cause] = Cause.HEALTHY


@dataclasses.dataclass(frozen=True)
class VpnBlocking:
    """A VPN is up and is the likely reason captive sign-in cannot complete."""

    interface_name: str
    is_full_tunnel: bool
    cause: ClassVar[Cause] = Cause.VPN_BLOCKING


@dataclasses.dataclass(frozen=True)
class DnsHijack:
    """System resolver and an independent resolver disagree for the same host."""

    host_probed: str
    system_answer: str
    doh_answer: str
    cause: ClassVar[Cause] = Cause.DNS_HIJACK


@dataclasses.dataclass(frozen=True)
class HttpProxyBlocking:
    """An HTTP proxy is configured and is eating the captive redirect."""

    description: str
    cause: ClassVar[Cause] = Cause.HTTP_PROXY_BLOCKING


@dataclasses.dataclass(frozen=True)
class HttpsOnlyCaptive:
    """Cleartext HTTP works but HTTPS is blocked or intercepted."""

    https_error_message: str
    cause: ClassVar[Cause] = Cause.HTTPS_ONLY_CAPTIVE


@dataclasses.dataclass(frozen=True)
class NoDnsServers:
    """DHCP handed this network zero DNS servers — a half-broken connect."""

    cause: ClassVar[Cause] = Cause.NO_DNS_SERVERS


@dataclasses.dataclass(frozen=True)
class PortalRedirectLoop:
    """The sign-in redirect chain revisits a URL it already issued."""

    chain: tuple[str, ...]
    cause: ClassVar[Cause] = Cause.PORTAL_REDIRECT_LOOP


@dataclasses.dataclass(frozen=True)
class ClockSkew:
    """Device clock disagrees with the gateway's Date header beyond tolerance."""

    skew_seconds: int
    cause: ClassVar[Cause] = Cause.CLOCK_SKEW


@dataclasses.dataclass(frozen=True)
class Inconclusive:
    """No finding; carries the raw probe errors so a human can read them."""

    probe_errors: tuple[str, ...]
    cause: ClassVar[Cause] = Cause.INCONCLUSIVE


DiagnosticReport = Union[
    Healthy,
    VpnBlocking,
    DnsHijack,
    HttpProxyBlocking,
    HttpsOnlyCaptive,
    NoDnsServers,
    PortalRedirectLoop,
    ClockSkew,
    Inconclusive,
]


class ActionId:
    """Action identifiers. Mirrors Kotlin `RecommendedAction.Ids`.

    Per D1 the engine never applies a fix — it names one, and the UI layer
    decides how to surface it.
    """

    PAUSE_VPN = "pause_vpn"
    DISABLE_HTTP_PROXY = "disable_http_proxy"
    RECONNECT_NETWORK = "reconnect_network"
    OPEN_DATE_TIME_SETTINGS = "open_date_time_settings"


@dataclasses.dataclass(frozen=True)
class RecommendedAction:
    """A step the user must take. Both fields None means 'nothing actionable'."""

    action_id: Optional[str] = None
    instruction: Optional[str] = None


NO_ACTION = RecommendedAction()
