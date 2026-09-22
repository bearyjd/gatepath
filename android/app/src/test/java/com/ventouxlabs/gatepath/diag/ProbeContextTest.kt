package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM tests for the shared probe helpers in `ProbeContext.kt`.
 *
 * [defaultRouteNotCaptiveReport] is `internal`, so this compiles only because
 * run-jvm-tests.sh passes -Xfriend-paths.
 */
class ProbeContextTest {

    private fun ctx(defaultRouteBypassesCaptive: Boolean?, activeProbe: suspend () -> ProbeResult) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        defaultRouteBypassesCaptive = defaultRouteBypassesCaptive,
        activeProbe = activeProbe,
    )

    @Test
    fun `null field measures via activeProbe and resolves true on Validated`() = runBlocking {
        var calls = 0
        val context = ctx(defaultRouteBypassesCaptive = null) { calls++; ProbeResult.Validated }

        assertTrue(context.defaultRouteBypassesCaptiveResolved())
        // Memoized: a second and third call must not measure again.
        assertTrue(context.defaultRouteBypassesCaptiveResolved())
        assertTrue(context.defaultRouteBypassesCaptiveResolved())
        assertEquals(1, calls)
    }

    @Test
    fun `null field measures via activeProbe and resolves false on Portal`() = runBlocking {
        val context = ctx(defaultRouteBypassesCaptive = null) { ProbeResult.Portal("http://portal.test/login") }

        assertFalse(context.defaultRouteBypassesCaptiveResolved())
    }

    @Test
    fun `non-null field never invokes activeProbe`() = runBlocking {
        var called = false
        val trueContext = ctx(defaultRouteBypassesCaptive = true) { called = true; ProbeResult.Validated }
        assertTrue(trueContext.defaultRouteBypassesCaptiveResolved())
        assertFalse(called)

        val falseContext = ctx(defaultRouteBypassesCaptive = false) { called = true; ProbeResult.Validated }
        assertFalse(falseContext.defaultRouteBypassesCaptiveResolved())
        assertFalse(called)
    }

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
