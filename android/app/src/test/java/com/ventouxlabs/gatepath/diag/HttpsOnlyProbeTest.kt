package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HttpsOnlyProbeTest {

    private var fetchedUrl: String? = null

    private fun ctx(http: ProbeResult, httpsResult: HttpFetchResult, defaultRouteBypassesCaptive: Boolean = false) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        defaultRouteBypassesCaptive = defaultRouteBypassesCaptive,
        probeUrl = "http://portal.test/probe",
        httpFetch = { url, _ ->
            fetchedUrl = url
            httpsResult
        },
        activeProbe = { http },
    )

    @Test
    fun `http fine but https reset reports HttpsOnlyCaptive against the https url`() = runBlocking {
        val report = HttpsOnlyProbe().run(
            ctx(ProbeResult.Validated, HttpFetchResult(null, null, null, null, "Connection reset")),
        )
        assertTrue(report is DiagnosticReport.HttpsOnlyCaptive)
        assertEquals("Connection reset", (report as DiagnosticReport.HttpsOnlyCaptive).httpsErrorMessage)
        assertEquals("https://portal.test/probe", fetchedUrl)
    }

    @Test
    fun `http and https both working is Healthy`() = runBlocking {
        val report = HttpsOnlyProbe().run(
            ctx(ProbeResult.Validated, HttpFetchResult(204, null, null, null, null)),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `http still captive is Healthy - nothing new to report`() = runBlocking {
        val report = HttpsOnlyProbe().run(
            ctx(ProbeResult.Portal("http://portal.test/portal"), HttpFetchResult(null, null, null, null, "reset")),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `http erroring is Healthy - the http probe owns that finding`() = runBlocking {
        val report = HttpsOnlyProbe().run(
            ctx(ProbeResult.Error("EPERM"), HttpFetchResult(null, null, null, null, "reset")),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `declines without probing when the default route is not the captive network`() = runBlocking {
        var activeProbeCalled = false
        val base = ctx(ProbeResult.Validated, HttpFetchResult(204, null, null, null, null), defaultRouteBypassesCaptive = true)
        val report = HttpsOnlyProbe().run(
            base.copy(activeProbe = { activeProbeCalled = true; ProbeResult.Validated }),
        )
        assertTrue(report is DiagnosticReport.Inconclusive)
        assertTrue(
            (report as DiagnosticReport.Inconclusive).probeErrors.single()
                .contains("default route is not the captive network"),
        )
        assertEquals(false, activeProbeCalled)
    }
}
