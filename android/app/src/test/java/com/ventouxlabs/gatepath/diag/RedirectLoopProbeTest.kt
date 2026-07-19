package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RedirectLoopProbeTest {

    private fun redirect(to: String) = HttpFetchResult(302, to, null, null, null)
    private fun ok204() = HttpFetchResult(204, null, null, null, null)
    private fun page200() = HttpFetchResult(200, null, null, "<html>portal</html>", null)

    private fun ctx(responses: Map<String, HttpFetchResult>) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        probeUrl = "http://portal.test/probe",
        httpFetch = { url, _ ->
            responses[url] ?: HttpFetchResult(null, null, null, null, "unexpected url: $url")
        },
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `two-node cycle is detected with the chain ending at the repeat`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(
                mapOf(
                    "http://portal.test/probe" to redirect("http://portal.test/a"),
                    "http://portal.test/a" to redirect("http://portal.test/b"),
                    "http://portal.test/b" to redirect("http://portal.test/a"),
                ),
            ),
        )
        assertTrue(report is DiagnosticReport.PortalRedirectLoop)
        val chain = (report as DiagnosticReport.PortalRedirectLoop).chain
        assertEquals(
            listOf("http://portal.test/probe", "http://portal.test/a", "http://portal.test/b", "http://portal.test/a"),
            chain,
        )
    }

    @Test
    fun `relative Location headers are resolved against the current url`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(
                mapOf(
                    "http://portal.test/probe" to redirect("/a"),
                    "http://portal.test/a" to redirect("/a"),
                ),
            ),
        )
        assertTrue(report is DiagnosticReport.PortalRedirectLoop)
    }

    @Test
    fun `chain ending in a page is Healthy`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(
                mapOf(
                    "http://portal.test/probe" to redirect("http://portal.test/portal"),
                    "http://portal.test/portal" to page200(),
                ),
            ),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `validated 204 is Healthy`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(mapOf("http://portal.test/probe" to ok204())),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `first fetch failing is Inconclusive with the error`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(mapOf("http://portal.test/probe" to HttpFetchResult(null, null, null, null, "connect timed out"))),
        )
        assertTrue(report is DiagnosticReport.Inconclusive)
        assertTrue((report as DiagnosticReport.Inconclusive).probeErrors.single().contains("connect timed out"))
    }

    @Test
    fun `long non-repeating chain gives up as Healthy at the hop cap`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(
                mapOf(
                    "http://portal.test/probe" to redirect("http://portal.test/1"),
                    "http://portal.test/1" to redirect("http://portal.test/2"),
                    "http://portal.test/2" to redirect("http://portal.test/3"),
                    "http://portal.test/3" to redirect("http://portal.test/4"),
                    "http://portal.test/4" to redirect("http://portal.test/5"),
                    "http://portal.test/5" to redirect("http://portal.test/6"),
                ),
            ),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }
}
