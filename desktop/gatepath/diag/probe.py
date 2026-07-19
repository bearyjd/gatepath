"""Probe protocol and the immutable context handed to every probe.

Mirror of Android `DiagnosticProbe.kt` + `ProbeContext.kt`. The context is
pure data plus injected callables, so every probe is directly unit-testable
with fakes and the package needs no I/O imports. Whoever runs the engine
(`gatepath.diag_context`) is responsible for filling these in from the real
system.

Deliberately absent versus Android: `is_private_dns_active` (an Android
system setting), `has_validated_cellular` (no cellular), and
`default_route_bypasses_captive` (Android has a bound-vs-unbound socket
distinction; desktop isolates with netns at the OS level and has no
per-request binding to bypass).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Callable, Optional, Protocol

from gatepath.diag.report import DiagnosticReport

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


class Probe(Protocol):
    """One axis of captive-portal diagnosis.

    Probes are stateless, must not mutate the context, and must complete
    inside the engine's per-probe budget. Network access goes through the
    context's injected callables, never directly.
    """

    name: str

    def run(self, ctx: ProbeContext) -> DiagnosticReport: ...
