package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnProbeTest {

    private fun ctx(vpnInterfaces: List<String>, tailscaleFullTunnel: Boolean = false) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = vpnInterfaces,
        isTailscaleFullTunnel = tailscaleFullTunnel,
        dnsServerCount = 1,
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `vpn interface present emits VpnBlocking with first interface`() = runBlocking {
        val report = VpnProbe().run(ctx(vpnInterfaces = listOf("tun0", "wg0")))
        assertTrue(report is DiagnosticReport.VpnBlocking)
        report as DiagnosticReport.VpnBlocking
        assertEquals("tun0", report.interfaceName)
        assertEquals(false, report.isFullTunnel)
    }

    @Test
    fun `tailscale exit node marks full tunnel`() = runBlocking {
        val report = VpnProbe().run(
            ctx(vpnInterfaces = listOf("tailscale0"), tailscaleFullTunnel = true),
        )
        report as DiagnosticReport.VpnBlocking
        assertEquals(true, report.isFullTunnel)
    }

    @Test
    fun `tailscale full tunnel without a detected interface still reports`() = runBlocking {
        val report = VpnProbe().run(ctx(vpnInterfaces = emptyList(), tailscaleFullTunnel = true))
        report as DiagnosticReport.VpnBlocking
        assertEquals("tailscale", report.interfaceName)
        assertEquals(true, report.isFullTunnel)
    }

    @Test
    fun `no vpn emits Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, VpnProbe().run(ctx(vpnInterfaces = emptyList())))
    }
}
