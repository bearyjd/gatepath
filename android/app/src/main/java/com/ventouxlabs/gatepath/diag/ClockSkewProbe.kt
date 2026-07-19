package com.ventouxlabs.gatepath.diag

import kotlin.math.abs

/** Tolerance before a clock difference is a finding; captive gateways are rarely this wrong. */
private const val SKEW_TOLERANCE_SECONDS = 300L

/**
 * Compares the device clock ([ProbeContext.nowEpochMillis]) against the
 * gateway's HTTP `Date` header (one [ProbeContext.httpFetch] of the probe
 * URL). A clock off by more than [SKEW_TOLERANCE_SECONDS] breaks TLS
 * certificate validation, making HTTPS portal pages fail in ways users read
 * as "the Wi-Fi is broken."
 *
 * The gateway's own clock could be the wrong one — the report says the two
 * disagree, and the recommended action (enable automatic date & time) is
 * safe either way. Missing header or failed fetch is Healthy: absence of
 * evidence, and other probes surface unreachability better.
 */
class ClockSkewProbe : DiagnosticProbe {
    override val name = "clock_skew"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        val result = ctx.httpFetch(ctx.probeUrl, null)
        val gatewayMs = result.dateHeaderEpochMillis ?: return DiagnosticReport.Healthy
        val skewSeconds = abs(ctx.nowEpochMillis() - gatewayMs) / 1000
        return if (skewSeconds > SKEW_TOLERANCE_SECONDS) {
            DiagnosticReport.ClockSkew(skewSeconds = skewSeconds)
        } else {
            DiagnosticReport.Healthy
        }
    }
}
