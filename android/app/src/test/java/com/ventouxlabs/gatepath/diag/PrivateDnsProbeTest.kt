package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PrivateDnsProbeTest {

    private fun ctx(active: Boolean, resolver: String? = null) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = active,
        privateDnsServer = resolver,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `private DNS active emits PrivateDnsBlocking with resolver host`() = runBlocking {
        val report = PrivateDnsProbe().run(ctx(active = true, resolver = "dns.cloudflare.com"))
        assertTrue(report is DiagnosticReport.PrivateDnsBlocking)
        assertEquals("dns.cloudflare.com", (report as DiagnosticReport.PrivateDnsBlocking).resolverHost)
    }

    @Test
    fun `private DNS active without resolver hostname is still PrivateDnsBlocking`() = runBlocking {
        val report = PrivateDnsProbe().run(ctx(active = true, resolver = null))
        assertTrue(report is DiagnosticReport.PrivateDnsBlocking)
        assertEquals(null, (report as DiagnosticReport.PrivateDnsBlocking).resolverHost)
    }

    @Test
    fun `private DNS off emits Healthy`() = runBlocking {
        val report = PrivateDnsProbe().run(ctx(active = false))
        assertEquals(DiagnosticReport.Healthy, report)
    }
}
