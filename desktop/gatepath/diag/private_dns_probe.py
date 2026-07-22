"""Reports strict DNS-over-TLS (Private DNS) that is likely blocking sign-in.

Reads `ProbeContext.private_dns_active` and (if true) returns
`PrivateDnsBlocking` carrying the configured resolver host. Strict DoT breaks
captive DNS: the resolver can't reach its DoT endpoint until the user signs
in, but signing in needs DNS — a chicken-and-egg deadlock.

Why this is its own probe even though it's just a field read: keeping the
decision in the engine's probe list means the audit log records that we
*checked*, not merely that nothing was found; and any future DoT/DoH/DoQ
splitting logic lives here in one place. Mirror of Android `PrivateDnsProbe.kt`.

Pure — no I/O and no platform imports. The systemd-resolved detection that
populates the context fields lives in `diag_context.py`, outside this package.
"""

from __future__ import annotations

from gatepath.diag.probe import ProbeContext
from gatepath.diag.report import DiagnosticReport, Healthy, PrivateDnsBlocking


class PrivateDnsProbe:
    name = "private_dns"

    def run(self, ctx: ProbeContext) -> DiagnosticReport:
        if ctx.private_dns_active:
            return PrivateDnsBlocking(resolver_host=ctx.private_dns_server)
        return Healthy()
