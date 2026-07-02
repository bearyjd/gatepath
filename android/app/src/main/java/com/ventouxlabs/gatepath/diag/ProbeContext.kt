package com.ventouxlabs.gatepath.diag

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
    val activeProbe: suspend () -> ProbeResult,
)
