package com.ventouxlabs.gatepath.diag

/**
 * Reads [ProbeContext.hasValidatedCellular] and returns
 * [DiagnosticReport.CellularFallback] when validated cellular is up while the
 * WiFi is stuck captive — mobile data masks the captive state, so pages load
 * but the portal never appears.
 *
 * No network call — completes synchronously inside the per-probe budget.
 */
class CellularFallbackProbe : DiagnosticProbe {
    override val name = "cellular_fallback"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport =
        if (ctx.hasValidatedCellular) {
            DiagnosticReport.CellularFallback(cellularValidated = true)
        } else {
            DiagnosticReport.Healthy
        }
}
