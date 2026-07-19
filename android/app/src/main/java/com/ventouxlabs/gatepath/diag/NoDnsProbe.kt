package com.ventouxlabs.gatepath.diag

/**
 * Reads [ProbeContext.dnsServerCount] and returns
 * [DiagnosticReport.NoDnsServers] when DHCP handed the network zero DNS
 * servers — a half-broken connect where the captive redirect can never
 * resolve.
 *
 * No network call — completes synchronously inside the per-probe budget.
 */
class NoDnsProbe : DiagnosticProbe {
    override val name = "no_dns"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport =
        if (ctx.dnsServerCount == 0) {
            DiagnosticReport.NoDnsServers
        } else {
            DiagnosticReport.Healthy
        }
}
