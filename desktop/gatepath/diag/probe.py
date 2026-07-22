"""Probe protocol and the immutable context handed to every probe.

Mirror of Android `DiagnosticProbe.kt` + `ProbeContext.kt`. The context is
pure data plus injected callables, so every probe is directly unit-testable
with fakes and the package needs no I/O imports. Whoever runs the engine
(`gatepath.diag_context`) is responsible for filling these in from the real
system.

Deliberately absent versus Android: `has_validated_cellular` (desktop has no
cellular radio to fall back onto). `private_dns_active` IS present — desktop
detects strict systemd-resolved DNS-over-TLS (see `diag_context.py`).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Callable, Optional, Protocol

from gatepath.diag.report import DiagnosticReport, Inconclusive

if TYPE_CHECKING:
    # Type-only import: erased at runtime, so `diag/` keeps its
    # no-platform-import guarantee while `active_probe` stays a properly
    # typed seam rather than `Any`.
    from gatepath.portal_probe import ProbeResult


@dataclasses.dataclass(frozen=True)
class HttpFetchResult:
    """Outcome of one no-follow GET. `error` is set iff no status was obtained.

    Declared here rather than beside the fetcher so `diag/` never imports the
    I/O module — the fetcher depends on this package, not the reverse.
    """

    status_code: Optional[int]
    location: Optional[str]
    date_epoch_seconds: Optional[float]
    body: Optional[str]
    error: Optional[str]


@dataclasses.dataclass(frozen=True)
class VpnDetail:
    """A detected VPN interface, split into the parts a probe reports on."""

    name: str
    is_full_tunnel: bool


@dataclasses.dataclass(frozen=True)
class ProbeContext:
    """Immutable snapshot of network state plus injected capabilities."""

    interface_name: str
    probe_url: str
    vpn_interfaces: tuple[VpnDetail, ...]
    http_proxy_description: Optional[str]
    dns_server_count: int
    http_fetch: Callable[[str, Optional[str]], HttpFetchResult]
    resolve_host: Callable[[str], tuple[str, ...]]
    now_epoch_seconds: Callable[[], float]
    active_probe: Callable[[], "ProbeResult"]
    # `active_probe` above has no default, so a defaulted field must go here
    # at the end — putting it any earlier (e.g. right after `dns_server_count`,
    # which would otherwise mirror Android's field order) raises TypeError at
    # class-definition time, since dataclasses forbid a defaulted field
    # before a non-defaulted one.
    #
    # `True` when the default route demonstrably is not the captive network
    # (e.g. a split-tunnel VPN's fallback probe got a 204 through it). Probes
    # that must interrogate the captive path itself must not report a finding
    # in this state — see `default_route_not_captive_report`. Mirror of
    # Android `ProbeContext.defaultRouteBypassesCaptive`.
    default_route_bypasses_captive: bool = False
    # Strict DNS-over-TLS (Private DNS) state, populated by `diag_context`.
    # `private_dns_active` is `True` only when the resolver enforces DoT (it
    # cannot silently downgrade to plaintext); `private_dns_server` is the
    # configured resolver host, if known. Mirror of Android
    # `ProbeContext.isPrivateDnsActive` / `ProbeContext.privateDnsServer`.
    private_dns_active: bool = False
    private_dns_server: Optional[str] = None


def default_route_not_captive_report(probe_name: str) -> DiagnosticReport:
    """Standard `Inconclusive` for a probe that can only answer by talking to
    the captive network, when the default route isn't it. Honest "didn't
    test" beats a green "no problem found" for a check that never ran.
    Mirror of Android `defaultRouteNotCaptiveReport`.
    """
    return Inconclusive(
        probe_errors=(f"{probe_name}: default route is not the captive network — this check would test the wrong path",),
    )


class Probe(Protocol):
    """One axis of captive-portal diagnosis.

    Probes are stateless, must not mutate the context, and must complete
    inside the engine's per-probe budget. Network access goes through the
    context's injected callables, never directly.
    """

    name: str

    def run(self, ctx: ProbeContext) -> DiagnosticReport: ...
