package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult

/**
 * Re-runs the active HTTP connectivity probe via [ProbeContext.activeProbe]
 * (which is bound to the captive [android.net.Network]) and reports whether
 * the path is now usable.
 *
 * Engine is invoked from a `CaptivePortalSuspected` event — meaning the
 * monitoring layer already ran bind + userspace probes and both failed. This
 * probe runs a fresh bind probe inside the diagnostic battery so:
 *   - if state has cleared (transient race), we surface Healthy
 *   - if the probe still errors, we carry the raw message into [DiagnosticReport.Inconclusive]
 *     so the user (or developer reading audit logs) can see what's failing
 *
 * In Phase 4 this probe will fan out to test HTTPS specifically and
 * distinguish "captive blocks HTTPS" from "no internet at all"; the current
 * shape is the minimum that lets the engine carry network errors into the
 * report stream.
 */
class HttpProbe : DiagnosticProbe {
    override val name = "http"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport =
        when (val r = ctx.activeProbe()) {
            is ProbeResult.Validated -> DiagnosticReport.Healthy
            is ProbeResult.Portal -> DiagnosticReport.Healthy
            is ProbeResult.Error -> DiagnosticReport.Inconclusive(
                probeErrors = listOf("http probe: ${r.message}"),
            )
        }
}
