package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.CONNECTIVITY_CHECK_URL
import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Snapshot of network state + a callable for active probes, passed to every
 * [DiagnosticProbe.run].
 *
 * Pure data — no `LinkProperties` or `Network` reference — so probes are
 * directly JVM-testable. The captive-portal-monitoring layer is responsible
 * for collecting these fields from the platform (`LinkProperties`,
 * `VpnDetector`, `ConnectivityManager`) before invoking the engine.
 *
 * @property activeProbe Suspending callable that performs an HTTP probe over
 *   the captive [Network] (passing the [Network] is the responsibility of the
 *   caller; the closure captures it). Returning [ProbeResult] keeps the probe
 *   protocol shared with the existing [com.ventouxlabs.gatepath.network.PortalProbe].
 */
data class ProbeContext(
    val networkId: String,
    val isPrivateDnsActive: Boolean,
    val privateDnsServer: String?,
    val httpProxyDescription: String?,
    val vpnInterfaces: List<String>,
    val isTailscaleFullTunnel: Boolean,
    val dnsServerCount: Int,
    /**
     * `true` if some *other* network is cellular AND validated right now —
     * i.e. mobile data is silently carrying traffic while the user thinks
     * they're on the captive WiFi.
     */
    val hasValidatedCellular: Boolean = false,

    /**
     * `true` when the default route demonstrably is not the captive network
     * (the fallback probe got a 204 through it); `false` when it demonstrably
     * is; `null` when this was never measured (e.g. no fallback probe ran).
     * Probes that interrogate the captive path itself must not report a
     * finding without knowing which of these holds — read this only via
     * [defaultRouteBypassesCaptiveResolved], never directly, so a `null` is
     * measured rather than silently treated as "unknown, so decline" (see
     * that function's doc for why declining on `null` is a detection
     * regression, not a safe default).
     */
    val defaultRouteBypassesCaptive: Boolean? = null,

    /** URL the monitor's own connectivity probe uses (debug builds may override — see AppModule). */
    val probeUrl: String = CONNECTIVITY_CHECK_URL,

    /**
     * Single no-follow GET over the captive network. Defaults to a stub so
     * context-only test fixtures need not wire it; network probes treat the
     * stub's error as Inconclusive-grade evidence, not a finding.
     */
    val httpFetch: suspend (url: String, accept: String?) -> HttpFetchResult =
        { _, _ -> HttpFetchResult(null, null, null, null, "httpFetch not wired") },

    /** System-resolver lookup (A/AAAA string forms); empty = resolution failed. */
    val resolveHost: suspend (host: String) -> List<String> = { emptyList() },

    /** Injectable clock for skew math in tests. */
    val nowEpochMillis: () -> Long = System::currentTimeMillis,

    val activeProbe: suspend () -> ProbeResult,
) {
    // Not a constructor property: excluded from equals/hashCode/copy/toString,
    // so `copy()` (as every probe test's `base.copy(...)` does) starts a fresh,
    // unmeasured memo rather than inheriting a stale one from the original
    // instance. Per-instance, not per-probe: [DiagnosticProbe] forbids a probe
    // from retaining state across calls, but this state belongs to the shared
    // [ProbeContext] one engine run builds and passes to every probe, so a
    // battery of probes that all ask the same question pays for the
    // measurement once, not once per probe.
    private val resolveMutex = Mutex()
    private var resolvedMemo: Boolean? = null

    /**
     * The honest answer to "does the default route bypass the captive
     * network", resolved rather than declined. Returns
     * [defaultRouteBypassesCaptive] directly when it is non-null. When it is
     * `null` — no fallback probe ran for this incident — measures it once via
     * [activeProbe]: a [ProbeResult.Validated] result means the default route
     * reached validation on its own, independent of the captive network, i.e.
     * it bypasses it.
     *
     * This exists because `null` used to mean "decline" for every probe that
     * reads [defaultRouteBypassesCaptive] (see [defaultRouteNotCaptiveReport]).
     * But the monitor emits `null` precisely when it skipped the fallback
     * probe, which happens on the same path — a captive-network incident —
     * that DNS-hijack, HTTPS-only-captive and redirect-loop detection exist
     * to cover. Declining there made those three probes silently no-op on
     * every real incident. Measuring lazily, once, and caching the result
     * restores detection without repeating the probe per caller.
     *
     * Guarded by [resolveMutex] so two probes racing to resolve the same
     * unmeasured context don't each trigger their own [activeProbe] call.
     */
    suspend fun defaultRouteBypassesCaptiveResolved(): Boolean {
        defaultRouteBypassesCaptive?.let { return it }
        resolveMutex.withLock {
            resolvedMemo?.let { return it }
            val measured = activeProbe() is ProbeResult.Validated
            resolvedMemo = measured
            return measured
        }
    }
}

/**
 * Standard `Inconclusive` for a probe that can only answer by talking to the
 * captive network, when the default route isn't it. Honest "didn't test"
 * beats a green "no problem found" for a check that never ran.
 */
internal fun defaultRouteNotCaptiveReport(probeName: String): DiagnosticReport =
    DiagnosticReport.Inconclusive(
        listOf("$probeName: default route is not the captive network — this check would test the wrong path"),
    )
