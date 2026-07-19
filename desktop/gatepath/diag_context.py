"""Assembles a `ProbeContext` from the real system, and declares the default
diagnostic battery.

This module lives outside `gatepath.diag` deliberately: it is the *only*
place that touches env vars, `/etc/resolv.conf`, sockets, and the VPN
detector. Everything in `diag/` stays pure data plus injected callables (see
`gatepath.diag.probe.ProbeContext`) and is testable with fakes; this module
is where those callables get their real, I/O-performing implementations.
Mirrors Android's `DiagnosticModule` — the single place probe membership is
declared for the platform.
"""

from __future__ import annotations

import os
import socket
import time
from typing import Mapping, Optional

from gatepath import http_fetcher, portal_probe, vpn_detector
from gatepath.diag.clock_skew_probe import ClockSkewProbe
from gatepath.diag.dns_hijack_probe import DnsHijackProbe
from gatepath.diag.engine import DiagnosticEngine
from gatepath.diag.http_probe import HttpProbe
from gatepath.diag.http_proxy_probe import HttpProxyProbe
from gatepath.diag.https_only_probe import HttpsOnlyProbe
from gatepath.diag.no_dns_probe import NoDnsProbe
from gatepath.diag.probe import HttpFetchResult, ProbeContext, VpnDetail
from gatepath.diag.redirect_loop_probe import RedirectLoopProbe
from gatepath.diag.vpn_probe import VpnProbe
from gatepath.portal_probe import ProbeResult


def _proxy_description(environ: Mapping[str, str]) -> Optional[str]:
    """Read the configured HTTP(S) proxy out of *environ*, or None.

    Precedence: `https_proxy` beats `http_proxy` (HTTPS is what the browser
    actually uses for most traffic today, so it's the more relevant signal),
    and for each scheme the lowercase spelling beats the uppercase one —
    lowercase is the de facto convention respected by curl/urllib/most
    *nix tooling, so when both are set (e.g. a shell that exported both for
    older-tool compatibility) the lowercase value is what the rest of the
    system is actually using.
    """
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        value = environ.get(key)
        if value:
            return value
    return None


def _count_dns_servers(path: str) -> int:
    """Count `nameserver` lines in the resolv.conf at *path*.

    Returns 0 when the file is missing or unreadable (permissions, a
    transient race with the file being rewritten, etc.) — an absent/broken
    resolv.conf is itself diagnostic signal (see `NoDnsProbe`), not an error
    to raise.

    Reads raw bytes and decodes with `errors="replace"` rather than opening
    in text mode: a hostile/corrupted resolv.conf can contain invalid UTF-8
    (this module exists specifically to survive hostile system state), and a
    strict decode would raise `UnicodeDecodeError` — a `ValueError`, not an
    `OSError` — straight through the `except` below. A lenient decode still
    counts any genuinely valid `nameserver` lines that happen to share the
    file with garbage bytes elsewhere, instead of discarding real signal
    because of unrelated corruption.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return 0

    lines = raw.decode("utf-8", errors="replace").splitlines()

    count = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.split()[:1] == ["nameserver"]:
            count += 1
    return count


def resolve_host(host: str) -> tuple[str, ...]:
    """Resolve *host* to its unique IPv4 addresses, or `()` on failure."""
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET)
    except OSError:
        return ()
    seen: dict[str, None] = {}
    for info in infos:
        address = info[4][0]
        seen.setdefault(address, None)
    return tuple(seen.keys())


def _http_fetch(url: str, accept: Optional[str]) -> HttpFetchResult:
    return http_fetcher.fetch(url, accept)


def now_epoch_seconds() -> float:
    return time.time()


def build_probe_context(
    interface_name: str,
    probe_url: str = portal_probe.CONNECTIVITY_CHECK_URL,
    *,
    environ: Mapping[str, str] = os.environ,
    resolv_conf_path: str = "/etc/resolv.conf",
) -> ProbeContext:
    """Assemble a `ProbeContext` for *interface_name* from real system state.

    Runs the connectivity probe once, up front, and reuses its result both as
    the injected `active_probe` callable's return value and to derive
    `default_route_bypasses_captive`.

    `default_route_bypasses_captive` derivation: desktop has no bound-vs-
    unbound probe pair to compare like Android does. Instead the signal comes
    from a contradiction between two independent observers. We are only
    running this diagnostic battery because NetworkManager already flagged
    *interface_name* as captive — that's the precondition for being here at
    all. If the connectivity probe *also* independently comes back
    `status == "validated"`, that means it reached the real internet without
    ever passing through a captive gateway — i.e. its request did not travel
    the captive path. So "validated" here does not mean "this network is
    healthy"; it means "whatever route this probe took is demonstrably not
    the captive one NetworkManager is complaining about" (perhaps a
    split-tunnel VPN, perhaps a race where the network validated between the
    NM flag and this probe running). Any other probe status — "portal" (the
    expected captive case) or "error" — leaves the flag False: only an
    explicit "validated" is evidence of bypass; the absence of that evidence
    is not evidence of the opposite.
    """
    probe_result: ProbeResult = portal_probe.probe(probe_url)

    vpn_interfaces = tuple(
        VpnDetail(name=vpn.name, is_full_tunnel=vpn.mode == "full_tunnel")
        for vpn in vpn_detector.detect_vpn_details()
    )

    return ProbeContext(
        interface_name=interface_name,
        probe_url=probe_url,
        vpn_interfaces=vpn_interfaces,
        http_proxy_description=_proxy_description(environ),
        dns_server_count=_count_dns_servers(resolv_conf_path),
        http_fetch=_http_fetch,
        resolve_host=resolve_host,
        now_epoch_seconds=now_epoch_seconds,
        active_probe=lambda: probe_result,
        default_route_bypasses_captive=probe_result.status == "validated",
    )


def default_engine() -> DiagnosticEngine:
    """The eight-probe battery run against every diagnosed interface.

    This is the single place probe membership is declared for desktop —
    mirrors Android's `DiagnosticModule`. Order here has no runtime meaning;
    `gatepath.diag.engine._RANK` is the sole source of truth for ranking.
    `HttpProbe()` is last, matching Android's `DiagnosticModule` ordering.
    """
    return DiagnosticEngine(
        probes=(
            VpnProbe(),
            DnsHijackProbe(),
            NoDnsProbe(),
            HttpProxyProbe(),
            RedirectLoopProbe(),
            ClockSkewProbe(),
            HttpsOnlyProbe(),
            HttpProbe(),
        )
    )
