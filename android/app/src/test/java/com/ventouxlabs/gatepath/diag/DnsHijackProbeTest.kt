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

    private fun ctx(
        systemAnswers: List<String>,
        doh: HttpFetchResult,
        defaultRouteBypassesCaptive: Boolean = false,
    ) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        defaultRouteBypassesCaptive = defaultRouteBypassesCaptive,
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

    @Test
    fun `declines without probing when the default route is not the captive network`() = runBlocking {
        var resolveHostCalled = false
        val base = ctx(
            systemAnswers = emptyList(),
            doh = HttpFetchResult(200, null, null, dohBody("1.2.3.4"), null),
            defaultRouteBypassesCaptive = true,
        )
        val report = DnsHijackProbe().run(
            base.copy(resolveHost = { resolveHostCalled = true; emptyList() }),
        )
        assertTrue(report is DiagnosticReport.Inconclusive)
        assertTrue(
            (report as DiagnosticReport.Inconclusive).probeErrors.single()
                .contains("default route is not the captive network"),
        )
        assertEquals(false, resolveHostCalled)
    }

    // The two helpers below are `internal`, so these tests only compile because
    // run-jvm-tests.sh passes -Xfriend-paths. They double as the guard on that
    // flag: drop it and this file stops compiling.

    @Test
    fun `doh parsing keeps A records and survives anything else`() {
        assertEquals(
            listOf("1.2.3.4", "5.6.7.8"),
            parseDohAddresses(dohBody("1.2.3.4", "5.6.7.8")),
        )
        // A hostile or broken gateway is the normal case for this parser, so
        // every malformed shape has to degrade to "no answers", never throw.
        assertEquals(emptyList<String>(), parseDohAddresses(""))
        assertEquals(emptyList<String>(), parseDohAddresses("not json at all"))
        assertEquals(emptyList<String>(), parseDohAddresses("""{"Status":0}"""))
        assertEquals(emptyList<String>(), parseDohAddresses("""[1,2,3]"""))
        // type 5 is CNAME, not A — filtered out rather than returned as an address.
        assertEquals(
            emptyList<String>(),
            parseDohAddresses("""{"Answer":[{"name":"x","type":5,"data":"elsewhere.example"}]}"""),
        )
    }

    @Test
    fun `private range detection covers the 172 block boundaries`() {
        for (address in listOf("10.0.0.1", "192.168.1.1", "127.0.0.1", "169.254.1.1")) {
            assertTrue(address, isPrivateOrLoopback(address))
        }
        // 172.16-31 is private; the two addresses either side of that window
        // are public, which is the easy place to be off by one.
        assertTrue("172.16.0.1", isPrivateOrLoopback("172.16.0.1"))
        assertTrue("172.31.255.254", isPrivateOrLoopback("172.31.255.254"))
        assertEquals(false, isPrivateOrLoopback("172.15.0.1"))
        assertEquals(false, isPrivateOrLoopback("172.32.0.1"))
        assertEquals(false, isPrivateOrLoopback("8.8.8.8"))
        assertEquals(false, isPrivateOrLoopback("172.notanumber.0.1"))
    }
}
