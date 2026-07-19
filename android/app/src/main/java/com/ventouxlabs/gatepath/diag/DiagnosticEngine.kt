package com.ventouxlabs.gatepath.diag

import kotlinx.coroutines.Deferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull

/** One probe's named outcome from an engine run. */
data class ProbeCheck(
    val probeName: String,
    val report: DiagnosticReport,
)

/** Result of one engine run — top finding + every probe's named outcome. */
data class DiagnosisResult(
    val top: DiagnosticReport,
    val checks: List<ProbeCheck>,
    val recommended: RecommendedAction,
)

/**
 * Orchestrator. Runs a battery of [DiagnosticProbe]s in parallel under a
 * total wall-clock budget (D3, confirmed 2026-05-08: 5s total, 2s per probe),
 * then ranks the non-`Healthy` reports and returns the top finding plus a
 * [RecommendedAction] descriptor.
 *
 * Engine is pure — no Android dependencies — so its ranking and
 * deadline-enforcement logic is JVM-testable directly. The platform glue lives
 * in `MainViewModel`, which calls [run] on each `CaptivePortalSuspected`
 * event from `CaptivePortalMonitor`.
 *
 * Single source of truth for severity ordering: [rankOf]. UI rendering MUST
 * NOT re-rank — if priority changes, change it here.
 */
class DiagnosticEngine(
    private val probes: List<DiagnosticProbe>,
    private val totalBudgetMs: Long = 5_000,
    private val perProbeBudgetMs: Long = 2_000,
) {

    @OptIn(ExperimentalCoroutinesApi::class)
    suspend fun run(ctx: ProbeContext): DiagnosisResult = coroutineScope {
        val deferred: List<Deferred<DiagnosticReport>> = probes.map { probe ->
            async {
                runCatching {
                    withTimeout(perProbeBudgetMs) { probe.run(ctx) }
                }.getOrElse { ex ->
                    DiagnosticReport.Inconclusive(
                        listOf("${probe.name}: ${ex.message ?: ex.javaClass.simpleName}"),
                    )
                }
            }
        }

        val reports = withTimeoutOrNull(totalBudgetMs) { deferred.awaitAll() }
            ?: deferred.mapIndexed { i, d ->
                if (d.isCompleted) {
                    d.getCompleted()
                } else {
                    d.cancel()
                    DiagnosticReport.Inconclusive(listOf("${probes[i].name}: total budget exceeded"))
                }
            }

        val checks = probes.mapIndexed { i, probe -> ProbeCheck(probe.name, reports[i]) }
        val nonHealthy = reports.filterNot { it is DiagnosticReport.Healthy }
        val ranked = nonHealthy.sortedByDescending(::rankOf)
        val top = ranked.firstOrNull() ?: DiagnosticReport.Healthy

        DiagnosisResult(
            top = top,
            checks = checks,
            recommended = recommendedActionFor(top),
        )
    }

    private fun rankOf(report: DiagnosticReport): Int = when (report) {
        is DiagnosticReport.VpnBlocking -> 100
        is DiagnosticReport.DnsHijack -> 90
        is DiagnosticReport.PrivateDnsBlocking -> 80
        is DiagnosticReport.HttpProxyBlocking -> 70
        is DiagnosticReport.SandboxedWebView -> 60
        is DiagnosticReport.CellularFallback -> 50
        is DiagnosticReport.HttpsOnlyCaptive -> 40
        is DiagnosticReport.Inconclusive -> 10
        is DiagnosticReport.Healthy -> 0
    }

    private fun recommendedActionFor(report: DiagnosticReport): RecommendedAction = when (report) {
        is DiagnosticReport.VpnBlocking -> RecommendedAction.UserAction(
            id = RecommendedAction.Ids.PAUSE_VPN,
            instruction = "Your VPN (${report.interfaceName}) is blocking captive sign-in. Pause it, sign in, then re-enable.",
        )
        is DiagnosticReport.PrivateDnsBlocking -> RecommendedAction.UserAction(
            id = RecommendedAction.Ids.OPEN_PRIVATE_DNS_SETTINGS,
            instruction = buildString {
                append("Private DNS is blocking captive sign-in")
                if (report.resolverHost != null) append(" (${report.resolverHost})")
                append(". Set Private DNS to Off (or Automatic) for this network in Settings.")
            },
        )
        is DiagnosticReport.HttpProxyBlocking -> RecommendedAction.UserAction(
            id = RecommendedAction.Ids.DISABLE_HTTP_PROXY,
            instruction = "An HTTP proxy (${report.description}) is intercepting the captive redirect. Disable it for this network.",
        )
        is DiagnosticReport.CellularFallback -> RecommendedAction.UserAction(
            id = RecommendedAction.Ids.DISABLE_CELLULAR,
            instruction = "Cellular is masking the captive WiFi state. Turn off mobile data temporarily, then retry.",
        )
        is DiagnosticReport.SandboxedWebView -> RecommendedAction.UserAction(
            id = RecommendedAction.Ids.APPLY_WEBVIEW_BRIDGE,
            instruction = "WebView routing didn't reach the captive interface. The bridge fix is queued for Phase 3.5.",
        )
        is DiagnosticReport.DnsHijack,
        is DiagnosticReport.HttpsOnlyCaptive,
        is DiagnosticReport.Inconclusive,
        is DiagnosticReport.Healthy -> RecommendedAction.NoActionAvailable
    }
}
