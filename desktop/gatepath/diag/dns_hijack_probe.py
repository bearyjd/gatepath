"""Compares the system resolver's answer against an independent DoH lookup.

Resolves `ProbeContext.probe_url`'s host via `ProbeContext.resolve_host` (the
system resolver) and separately via Cloudflare's DNS-over-HTTPS JSON API
(`ProbeContext.http_fetch`). A gateway that answers with its own private
address while the true record is public is hijacking DNS beyond the probe
endpoints — the aggressive-captive signature that also breaks HTTPS after
sign-in.

Verdict policy (conservative — false negatives over false alarms): host
unparseable or system resolve empty -> Inconclusive; DoH error/unparseable/no
public answer -> Healthy (DoH being blocked pre-login is normal captivity,
not hijack evidence); DnsHijack only when EVERY system answer is
private/loopback AND DoH returned at least one public address.

Declines with `Inconclusive` when `ProbeContext.default_route_bypasses_captive`
is set — the system resolver in that state belongs to the bypassing network
(VPN/cellular), not the captive one, so a hijack verdict here would be about
the wrong network.

Mirror of Android `DnsHijackProbe.kt`.
"""

from __future__ import annotations

import json
import urllib.parse

from gatepath.diag.probe import ProbeContext, default_route_not_captive_report
from gatepath.diag.report import DiagnosticReport, DnsHijack, Healthy, Inconclusive

# Cloudflare's DoH JSON endpoint, addressed by IP literal deliberately: the
# hostname form would itself need bootstrap resolution through the very
# system resolver this probe suspects of hijacking. 1.1.1.1 is in
# Cloudflare's certificate SAN, so TLS and the JSON API work identically.
DOH_ENDPOINT = "https://1.1.1.1/dns-query"
DOH_ACCEPT = "application/dns-json"

# DNS record type 1 = A. We only compare IPv4 answers.
_TYPE_A = 1


class DnsHijackProbe:
    name = "dns_hijack"

    def run(self, ctx: ProbeContext) -> DiagnosticReport:
        if ctx.default_route_bypasses_captive:
            return default_route_not_captive_report(self.name)

        host = urllib.parse.urlparse(ctx.probe_url).hostname
        if not host:
            return Inconclusive(probe_errors=(f"{self.name}: unparseable probe url",))

        system_answers = ctx.resolve_host(host)
        if not system_answers:
            return Inconclusive(
                probe_errors=(f"{self.name}: system resolver returned no answers for {host}",),
            )

        doh = ctx.http_fetch(f"{DOH_ENDPOINT}?name={host}&type=A", DOH_ACCEPT)
        doh_answers = _parse_doh_addresses(doh.body) if doh.body is not None else ()
        doh_public = tuple(addr for addr in doh_answers if not _is_private_or_loopback(addr))
        if doh.error is not None or not doh_public:
            return Healthy()

        all_system_private = all(_is_private_or_loopback(addr) for addr in system_answers)
        if all_system_private:
            return DnsHijack(
                host_probed=host,
                system_answer=system_answers[0],
                doh_answer=doh_public[0],
            )
        return Healthy()


def _parse_doh_addresses(body: str) -> tuple[str, ...]:
    """Extracts A-record `data` fields from a DoH JSON body; empty on any parse problem."""
    try:
        parsed = json.loads(body)
        answers = parsed.get("Answer") or []
        return tuple(
            answer["data"]
            for answer in answers
            if isinstance(answer, dict) and answer.get("type") == _TYPE_A and "data" in answer
        )
    except (json.JSONDecodeError, AttributeError, TypeError, KeyError):
        return ()


def _is_private_or_loopback(address: str) -> bool:
    """RFC1918 / loopback / link-local — the address ranges captive gateways
    answer with.

    IPv4-only, deliberately mirroring Android's limitation — the two engines
    must behave identically, so this is a shared documented gap, not
    something to "improve" with IPv6 handling on just one side.
    """
    if address.startswith(("10.", "192.168.", "127.", "169.254.")):
        return True
    if address.startswith("172."):
        parts = address.split(".")
        second = parts[1] if len(parts) > 1 else ""
        return second.isdigit() and 16 <= int(second) <= 31
    return False
