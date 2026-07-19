package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ClockSkewProbeTest {

    private val nowMs = 1_800_000_000_000L

    private fun ctx(dateHeaderMs: Long?, error: String? = null) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        probeUrl = "http://portal.test/probe",
        httpFetch = { _, _ ->
            if (error != null) HttpFetchResult(null, null, null, null, error)
            else HttpFetchResult(302, "http://portal.test/portal", dateHeaderMs, null, null)
        },
        nowEpochMillis = { nowMs },
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `device fifteen minutes ahead of gateway reports skew`() = runBlocking {
        val report = ClockSkewProbe().run(ctx(dateHeaderMs = nowMs - 900_000))
        assertTrue(report is DiagnosticReport.ClockSkew)
        assertEquals(900L, (report as DiagnosticReport.ClockSkew).skewSeconds)
    }

    @Test
    fun `device behind gateway also reports skew`() = runBlocking {
        val report = ClockSkewProbe().run(ctx(dateHeaderMs = nowMs + 900_000))
        assertTrue(report is DiagnosticReport.ClockSkew)
        assertEquals(900L, (report as DiagnosticReport.ClockSkew).skewSeconds)
    }

    @Test
    fun `skew inside the five-minute tolerance is Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, ClockSkewProbe().run(ctx(dateHeaderMs = nowMs - 200_000)))
    }

    @Test
    fun `missing Date header is Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, ClockSkewProbe().run(ctx(dateHeaderMs = null)))
    }

    @Test
    fun `fetch error is Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, ClockSkewProbe().run(ctx(dateHeaderMs = null, error = "timeout")))
    }
}
