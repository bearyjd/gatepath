package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM tests for [DiagnosticEngine]. No Android SDK or coroutines-test
 * dependency — uses real wall-clock against tightened budgets so the deadline
 * paths are exercised.
 */
class DiagnosticEngineTest {

    private val noopCtx = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        activeProbe = { ProbeResult.Validated },
    )

    private fun probe(name: String, report: DiagnosticReport, delayMs: Long = 0): DiagnosticProbe =
        object : DiagnosticProbe {
            override val name = name
            override suspend fun run(ctx: ProbeContext): DiagnosticReport {
                if (delayMs > 0) delay(delayMs)
                return report
            }
        }

    @Test
    fun `all-healthy yields Healthy with NoActionAvailable`() = runBlocking {
        val engine = DiagnosticEngine(
            probes = listOf(
                probe("p1", DiagnosticReport.Healthy),
                probe("p2", DiagnosticReport.Healthy),
            ),
        )
        val result = engine.run(noopCtx)
        assertEquals(DiagnosticReport.Healthy, result.top)
        assertEquals(RecommendedAction.NoActionAvailable, result.recommended)
    }

    @Test
    fun `top finding is highest-priority report`() = runBlocking {
        // Mixed: VPN (rank 100) wins over PrivateDns (rank 80) and HttpProxy (rank 70).
        val engine = DiagnosticEngine(
            probes = listOf(
                probe("dns", DiagnosticReport.PrivateDnsBlocking("dns.example")),
                probe("vpn", DiagnosticReport.VpnBlocking("tun0", isFullTunnel = true)),
                probe("proxy", DiagnosticReport.HttpProxyBlocking("proxy:3128")),
                probe("ok", DiagnosticReport.Healthy),
            ),
        )
        val result = engine.run(noopCtx)
        assertTrue(result.top is DiagnosticReport.VpnBlocking)
        assertTrue(result.recommended is RecommendedAction.UserAction)
        assertEquals(
            RecommendedAction.Ids.PAUSE_VPN,
            (result.recommended as RecommendedAction.UserAction).id,
        )
        assertEquals(4, result.checks.size)
    }

    @Test
    fun `per-probe timeout produces Inconclusive for that probe`() = runBlocking {
        val engine = DiagnosticEngine(
            probes = listOf(
                probe("slow", DiagnosticReport.PrivateDnsBlocking(null), delayMs = 500),
                probe("fast", DiagnosticReport.Healthy),
            ),
            perProbeBudgetMs = 100,
            totalBudgetMs = 1_000,
        )
        val result = engine.run(noopCtx)
        // The slow probe should be timed out → Inconclusive; fast probe → Healthy.
        // After filtering Healthy, only Inconclusive remains as non-Healthy → top.
        assertTrue("expected Inconclusive top, got ${result.top}", result.top is DiagnosticReport.Inconclusive)
        val msg = (result.top as DiagnosticReport.Inconclusive).probeErrors.joinToString()
        assertTrue("expected slow probe error message, got $msg", msg.contains("slow"))
    }

    @Test
    fun `total budget cancels straggling probes`() = runBlocking {
        val engine = DiagnosticEngine(
            probes = listOf(
                probe("straggler", DiagnosticReport.Healthy, delayMs = 5_000),
                probe("quick", DiagnosticReport.PrivateDnsBlocking("d.example")),
            ),
            perProbeBudgetMs = 10_000,  // intentionally large to force the TOTAL budget to be the gate
            totalBudgetMs = 200,
        )
        val result = engine.run(noopCtx)
        // Quick probe finished in time; straggler exceeded total budget.
        assertTrue("expected non-Healthy top, got ${result.top}",
            result.top is DiagnosticReport.PrivateDnsBlocking ||
                result.top is DiagnosticReport.Inconclusive)
        // Whatever else came back, the result list must contain a marker for the straggler.
        val joined = result.checks.joinToString { it.report::class.simpleName ?: "?" }
        assertTrue("expected straggler in results: $joined", result.checks.size == 2)
    }

    @Test
    fun `single PrivateDnsBlocking yields the right action descriptor`() = runBlocking {
        val engine = DiagnosticEngine(
            probes = listOf(probe("p", DiagnosticReport.PrivateDnsBlocking("dns.cloudflare.com"))),
        )
        val result = engine.run(noopCtx)
        val action = result.recommended as RecommendedAction.UserAction
        assertEquals(RecommendedAction.Ids.OPEN_PRIVATE_DNS_SETTINGS, action.id)
        assertTrue(
            "instruction should mention dns.cloudflare.com: ${action.instruction}",
            action.instruction.contains("dns.cloudflare.com"),
        )
    }

    @Test
    fun `checks carry the emitting probe's name in probe-list order`() = runBlocking {
        val engine = DiagnosticEngine(
            probes = listOf(
                probe("vpn", DiagnosticReport.VpnBlocking("tun0", isFullTunnel = true)),
                probe("ok", DiagnosticReport.Healthy),
            ),
        )
        val result = engine.run(noopCtx)
        assertEquals(listOf("vpn", "ok"), result.checks.map { it.probeName })
        assertTrue(result.checks[0].report is DiagnosticReport.VpnBlocking)
        assertEquals(DiagnosticReport.Healthy, result.checks[1].report)
    }

    @Test
    fun `Inconclusive ranks below all real findings`() = runBlocking {
        val engine = DiagnosticEngine(
            probes = listOf(
                probe("inc", DiagnosticReport.Inconclusive(listOf("nope"))),
                probe("dns", DiagnosticReport.PrivateDnsBlocking(null)),
            ),
        )
        val result = engine.run(noopCtx)
        assertTrue("expected PrivateDnsBlocking to win over Inconclusive", result.top is DiagnosticReport.PrivateDnsBlocking)
    }
}
