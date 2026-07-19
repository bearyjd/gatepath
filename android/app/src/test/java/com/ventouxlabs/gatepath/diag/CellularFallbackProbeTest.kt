package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CellularFallbackProbeTest {

    private fun ctx(hasValidatedCellular: Boolean) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        hasValidatedCellular = hasValidatedCellular,
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `validated cellular alongside captive wifi emits CellularFallback`() = runBlocking {
        val report = CellularFallbackProbe().run(ctx(hasValidatedCellular = true))
        assertTrue(report is DiagnosticReport.CellularFallback)
        assertEquals(true, (report as DiagnosticReport.CellularFallback).cellularValidated)
    }

    @Test
    fun `no validated cellular emits Healthy`() = runBlocking {
        assertEquals(
            DiagnosticReport.Healthy,
            CellularFallbackProbe().run(ctx(hasValidatedCellular = false)),
        )
    }
}
