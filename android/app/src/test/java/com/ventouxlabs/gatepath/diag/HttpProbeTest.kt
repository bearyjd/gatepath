package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HttpProbeTest {

    private fun ctx(probeResult: ProbeResult) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        activeProbe = { probeResult },
    )

    @Test
    fun `Validated probe maps to Healthy`() = runBlocking {
        val report = HttpProbe().run(ctx(ProbeResult.Validated))
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `Portal probe maps to Healthy - the captive path itself is reachable`() = runBlocking {
        val report = HttpProbe().run(ctx(ProbeResult.Portal("http://portal.example/login")))
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `Error probe maps to Inconclusive carrying the message`() = runBlocking {
        val report = HttpProbe().run(ctx(ProbeResult.Error("EPERM (Operation not permitted)")))
        assertTrue(report is DiagnosticReport.Inconclusive)
        val msg = (report as DiagnosticReport.Inconclusive).probeErrors.single()
        assertTrue("expected error message in: $msg", msg.contains("EPERM"))
    }
}
