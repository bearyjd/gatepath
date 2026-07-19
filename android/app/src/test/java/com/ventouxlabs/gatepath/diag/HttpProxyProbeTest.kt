package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HttpProxyProbeTest {

    private fun ctx(proxy: String?) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = proxy,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `configured proxy emits HttpProxyBlocking with description`() = runBlocking {
        val report = HttpProxyProbe().run(ctx(proxy = "proxy.corp:3128"))
        assertTrue(report is DiagnosticReport.HttpProxyBlocking)
        assertEquals("proxy.corp:3128", (report as DiagnosticReport.HttpProxyBlocking).description)
    }

    @Test
    fun `no proxy emits Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, HttpProxyProbe().run(ctx(proxy = null)))
    }
}
