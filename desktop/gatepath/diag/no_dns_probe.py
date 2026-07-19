"""Reports a network that DHCP handed zero DNS servers.

Reads `ProbeContext.dns_server_count` and returns `NoDnsServers` when it is
zero — a half-broken connect where the captive redirect can never resolve,
so every later probe would be chasing a symptom rather than the cause.

Mirror of Android `NoDnsProbe.kt`.

Context-only — no network access.
"""

from __future__ import annotations

from gatepath.diag.probe import ProbeContext
from gatepath.diag.report import DiagnosticReport, Healthy, NoDnsServers


class NoDnsProbe:
    name = "no_dns"

    def run(self, ctx: ProbeContext) -> DiagnosticReport:
        if ctx.dns_server_count == 0:
            return NoDnsServers()
        return Healthy()
