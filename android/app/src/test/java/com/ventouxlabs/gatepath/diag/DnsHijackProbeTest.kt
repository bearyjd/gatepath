package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DnsHijackProbeTest {

    private fun dohBody(vararg addresses: String): String {
        val answers = addresses.joinToString(",") { """{"name":"connectivitycheck.gstatic.com","type":1,"data":"$it"}""" }
        return """{"Status":0,"Answer":[$answers]}"""
    }

    private fun ctx(systemAnswers: List<String>, doh: HttpFetchResult) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        probeUrl = "http://connectivitycheck.gstatic.com/generate_204",
        httpFetch = { _, accept ->
            // The DoH request must ask for the JSON media type.
            if (accept == "application/dns-json") doh
            else HttpFetchResult(null, null, null, null, "wrong accept: $accept")
        },
        resolveHost = { systemAnswers },
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `private system answer with public doh answer is a hijack`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(
                systemAnswers = listOf("192.168.1.1"),
                doh = HttpFetchResult(200, null, null, dohBody("142.250.180.14"), null),
            ),
        )
        assertTrue(report is DiagnosticReport.DnsHijack)
        report as DiagnosticReport.DnsHijack
        assertEquals("connectivitycheck.gstatic.com", report.hostProbed)
        assertEquals("192.168.1.1", report.systemAnswer)
        assertEquals("142.250.180.14", report.doHAnswer)
    }

    @Test
    fun `matching public answers are Healthy`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(
                systemAnswers = listOf("142.250.180.14"),
                doh = HttpFetchResult(200, null, null, dohBody("142.250.180.14"), null),
            ),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `system resolution failure is Inconclusive`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(systemAnswers = emptyList(), doh = HttpFetchResult(200, null, null, dohBody("1.2.3.4"), null)),
        )
        assertTrue(report is DiagnosticReport.Inconclusive)
    }

    @Test
    fun `doh unreachable is Healthy - expected while captive`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(systemAnswers = listOf("10.0.0.1"), doh = HttpFetchResult(null, null, null, null, "timeout")),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `malformed doh json is Healthy, never a crash`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(systemAnswers = listOf("10.0.0.1"), doh = HttpFetchResult(200, null, null, "not json {", null)),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `public system answer is Healthy even if doh differs`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(
                systemAnswers = listOf("8.8.8.8"),
                doh = HttpFetchResult(200, null, null, dohBody("142.250.180.14"), null),
            ),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }
}
