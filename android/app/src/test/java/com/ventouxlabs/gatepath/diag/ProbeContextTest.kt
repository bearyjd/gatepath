package com.ventouxlabs.gatepath.diag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM tests for the shared probe helpers in `ProbeContext.kt`.
 *
 * [defaultRouteNotCaptiveReport] is `internal`, so this compiles only because
 * run-jvm-tests.sh passes -Xfriend-paths.
 */
class ProbeContextTest {

    /**
     * Three probes (HttpsOnly, RedirectLoop, DnsHijack) return this verbatim
     * when the default route isn't the captive network, so its shape is part
     * of all three contracts rather than one probe's private detail.
     */
    @Test
    fun `a skipped probe reports inconclusive, never healthy`() {
        val report = defaultRouteNotCaptiveReport("DnsHijack")

        // The whole point of the helper: a check that never ran must not be
        // able to read as a check that found nothing wrong.
        assertTrue(report.toString(), report is DiagnosticReport.Inconclusive)
        assertEquals(
            listOf(
                "DnsHijack: default route is not the captive network — " +
                    "this check would test the wrong path",
            ),
            (report as DiagnosticReport.Inconclusive).probeErrors,
        )
    }

    @Test
    fun `the reason names the probe that was skipped`() {
        // Callers pass their own `name`, and the diagnostics bundle shows these
        // verbatim, so a reader has to be able to tell which check was skipped.
        for (probeName in listOf("HttpsOnly", "RedirectLoop", "DnsHijack")) {
            val errors = (defaultRouteNotCaptiveReport(probeName) as DiagnosticReport.Inconclusive)
                .probeErrors
            assertEquals(1, errors.size)
            assertTrue(errors.single(), errors.single().startsWith("$probeName: "))
        }
    }
}
