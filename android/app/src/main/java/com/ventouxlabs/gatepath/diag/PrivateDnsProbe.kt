package com.ventouxlabs.gatepath.diag

/**
 * Reads [ProbeContext.isPrivateDnsActive] and (if true) returns
 * [DiagnosticReport.PrivateDnsBlocking].
 *
 * Why this is its own probe even though it's just a field read: keeping the
 * decision in the engine's probe list means the audit log records that we
 * checked, not just that we found nothing. If a future LinkProperties API
 * change splits Private DNS into multiple flags (e.g. DoT vs DoH vs DoQ),
 * the additional logic lives here in one place.
 *
 * No network call — completes synchronously inside the per-probe budget.
 */
class PrivateDnsProbe : DiagnosticProbe {
    override val name = "private_dns"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport =
        if (ctx.isPrivateDnsActive) {
            DiagnosticReport.PrivateDnsBlocking(resolverHost = ctx.privateDnsServer)
        } else {
            DiagnosticReport.Healthy
        }
}
