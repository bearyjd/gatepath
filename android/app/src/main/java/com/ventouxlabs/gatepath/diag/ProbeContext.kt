package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.CONNECTIVITY_CHECK_URL
import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult

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
     * finding in either the `true` or the `null` case — see
     * [defaultRouteNotCaptiveReport] and [defaultRouteNotMeasuredReport].
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
)

/**
 * Standard `Inconclusive` for a probe that can only answer by talking to the
 * captive network, when the default route isn't it. Honest "didn't test"
 * beats a green "no problem found" for a check that never ran.
 */
internal fun defaultRouteNotCaptiveReport(probeName: String): DiagnosticReport =
    DiagnosticReport.Inconclusive(
        listOf("$probeName: default route is not the captive network — this check would test the wrong path"),
    )

/**
 * Standard `Inconclusive` for a probe that needs to know whether the default
 * route bypasses the captive network, but that measurement
 * ([ProbeContext.defaultRouteBypassesCaptive]) was never taken — e.g. no
 * fallback probe ran for this incident. Declining honestly beats guessing
 * which path the check would actually be testing.
 */
internal fun defaultRouteNotMeasuredReport(probeName: String): DiagnosticReport =
    DiagnosticReport.Inconclusive(
        listOf("$probeName: default route bypass was not measured — this check would test an unknown path"),
    )
