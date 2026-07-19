package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

class NoDnsProbeTest {

    private fun ctx(dnsServerCount: Int) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = dnsServerCount,
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `zero DNS servers emits NoDnsServers`() = runBlocking {
        assertEquals(DiagnosticReport.NoDnsServers, NoDnsProbe().run(ctx(dnsServerCount = 0)))
    }

    @Test
    fun `at least one DNS server emits Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, NoDnsProbe().run(ctx(dnsServerCount = 1)))
    }
}
