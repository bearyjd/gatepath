package com.ventouxlabs.gatepath.diag

/**
 * One axis of captive-portal diagnosis.
 *
 * Probes are stateless — they receive a [ProbeContext] snapshot and return a
 * single [DiagnosticReport]. They MUST NOT mutate the context, retain state
 * across calls, or run longer than [DiagnosticEngine.perProbeBudgetMs]. Per-D3
 * (confirmed 2026-05-08) the engine enforces the per-probe deadline via
 * `withTimeout`; probes that genuinely need longer should be split.
 *
 * Active probes (those that touch the network) MUST go through
 * [ProbeContext.activeProbe] rather than calling out directly — that callable
 * carries the bound captive [android.net.Network], and routing through it is
 * what keeps Gatepath's traffic on the captive interface instead of leaking
 * via the default route.
 */
interface DiagnosticProbe {

    /** Stable identifier for the probe; used in audit logs. */
    val name: String

    suspend fun run(ctx: ProbeContext): DiagnosticReport
}
