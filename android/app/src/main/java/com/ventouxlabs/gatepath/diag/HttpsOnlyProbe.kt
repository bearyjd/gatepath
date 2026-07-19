package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult

/**
 * Detects captive setups that pass cleartext HTTP but kill HTTPS (TLS
 * interception, RST-on-443): re-runs the HTTP verdict via
 * [ProbeContext.activeProbe], and only when HTTP says *validated* does it try
 * the https-scheme variant of [ProbeContext.probeUrl] via
 * [ProbeContext.httpFetch]. HTTPS failing while HTTP works is the
 * [DiagnosticReport.HttpsOnlyCaptive] signature.
 *
 * While the network is still captive (HTTP → Portal) or broken (HTTP →
 * Error), HTTPS failing tells us nothing new — those cases are Healthy here
 * and owned by other probes. This is the "Phase 4 fan-out" HttpProbe's doc
 * deferred.
 */
class HttpsOnlyProbe : DiagnosticProbe {
    override val name = "https_only"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        when (ctx.activeProbe()) {
            is ProbeResult.Portal, is ProbeResult.Error -> return DiagnosticReport.Healthy
            is ProbeResult.Validated -> Unit
        }
        val httpsUrl = ctx.probeUrl.replaceFirst("http://", "https://")
        val https = ctx.httpFetch(httpsUrl, null)
        return when {
            https.error != null -> DiagnosticReport.HttpsOnlyCaptive(httpsErrorMessage = https.error)
            else -> DiagnosticReport.Healthy
        }
    }
}
