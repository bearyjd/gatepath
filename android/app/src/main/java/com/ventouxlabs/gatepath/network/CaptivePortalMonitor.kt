package com.ventouxlabs.gatepath.network

import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.launch

private const val TAG = "GatepathMonitor"

/** Events emitted by [CaptivePortalMonitor]. */
sealed interface NetworkEvent {
    /** A captive portal was detected at [portalUrl]. */
    data class CaptiveNetworkAvailable(
        val network: Network,
        val portalUrl: String,
    ) : NetworkEvent

    /**
     * A previously-captive network has transitioned to validated. This is the
     * SUCCESS signal of a portal sign-in: the user authenticated, the network
     * gained NET_CAPABILITY_VALIDATED, and the session can transition from
     * Active → Completed(PORTAL_COMPLETED).
     */
    data class NetworkValidated(val network: Network) : NetworkEvent

    /**
     * A network was observed as validated on first sight — no captive portal
     * detected. Use this to tell the user "you are on a regular WiFi, all good"
     * instead of leaving them staring at an unending "Monitoring…" screen.
     *
     * Distinct from [NetworkValidated]: this fires for networks that were never
     * captive in the first place (e.g. home WiFi). [NetworkValidated] fires
     * specifically when a captive network transitions to validated after a
     * successful sign-in.
     */
    data class NetworkObservedNoPortal(val network: Network) : NetworkEvent

    /**
     * A previously-captive network was lost (e.g. WiFi disconnect during
     * sign-in). Only emitted for networks we previously identified as captive,
     * not every network that disappears.
     */
    data class CaptiveNetworkLost(val network: Network) : NetworkEvent

    /**
     * A network looks captive (INTERNET present, NOT validated) but neither
     * the bind-probe path nor the userspace-fallback path could confirm the
     * portal. The [diagnostics] payload carries everything the UI needs to
     * walk the user through the troubleshooting pathway (VPN, Private DNS,
     * proxy, raw probe errors).
     */
    data class CaptivePortalSuspected(
        val network: Network,
        val diagnostics: NetworkDiagnostics,
    ) : NetworkEvent
}

/**
 * Wraps [ConnectivityManager.NetworkCallback] in a cold [Flow].
 *
 * Captive detection rule (per the original spec): a network is treated as
 * captive when it has `NET_CAPABILITY_INTERNET` but lacks
 * `NET_CAPABILITY_VALIDATED`. We confirm with two probe paths:
 *
 *   1. **Bind path** — `bindProcessToNetwork(network)` then
 *      `network.openConnection()`. Most authoritative. Fails with `EPERM` on
 *      captive networks because Android marks them restricted.
 *
 *   2. **Userspace fallback** — `URL.openConnection()` with no bind. Routes
 *      via the kernel's default route. Works when there's no VPN
 *      intercepting the default; fails when VPN is up because the tunnel
 *      can't reach the captive gateway.
 *
 * If both paths fail we emit [NetworkEvent.CaptivePortalSuspected] with a
 * full [NetworkDiagnostics] snapshot so the UI can guide the user (pause
 * VPN, use the system "Sign in to Wi-Fi" notification, watch out for
 * Private DNS / proxy interference).
 *
 * Lifecycle:
 * - Captive detected → emit `CaptiveNetworkAvailable`
 * - Captive then validated → emit `NetworkValidated` (success: user signed in)
 * - Captive then lost → emit `CaptiveNetworkLost`
 * - Probes both fail → emit `CaptivePortalSuspected` with diagnostics
 * - Validated network transitions are silent (no event for non-captive lifecycle)
 *
 * Collect this flow from a lifecycle-scoped coroutine (e.g. viewModelScope).
 */
class CaptivePortalMonitor(
    private val connectivityManager: ConnectivityManager,
    private val probe: PortalProbe = PortalProbe(),
    // URL Gatepath's own connectivity probe hits. Defaults to the standard
    // gstatic endpoint; debug builds may override it (see AppModule) so the
    // e2e harness can point it at its mock portal. Production always uses the
    // default.
    val probeUrl: String = CONNECTIVITY_CHECK_URL,
) {

    fun observe(): Flow<NetworkEvent> = callbackFlow {
        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .addTransportType(NetworkCapabilities.TRANSPORT_ETHERNET)
            .build()

        val ioScope = CoroutineScope(Dispatchers.IO)
        // Networks we have probed (or have a probe in flight for) — prevents
        // capability churn from queueing dozens of probes for the same net.
        val probed = java.util.concurrent.ConcurrentHashMap.newKeySet<Network>()
        // Networks we have emitted CaptiveNetworkAvailable for. Used to gate
        // both NetworkValidated (success signal) and CaptiveNetworkLost
        // (don't emit Lost for non-captive networks the caller never cared about).
        val captive = java.util.concurrent.ConcurrentHashMap.newKeySet<Network>()
        // Networks we have already reported as validated/no-portal so the UI
        // doesn't get spammed with NetworkObservedNoPortal events as
        // capabilities churn.
        val reportedNoPortal = java.util.concurrent.ConcurrentHashMap.newKeySet<Network>()
        // Last-seen capability summary per network. We log capability changes
        // only when this transitions, so logcat isn't drowned in churn.
        val lastCaps = java.util.concurrent.ConcurrentHashMap<Network, Pair<Boolean, Boolean>>()

        fun probeAndEmit(network: Network) {
            if (!probed.add(network)) return
            ioScope.launch {
                Log.d(TAG, "Probing network $network (bind path)")

                // PATH 1 — bind-probe. Most authoritative because the socket
                // is bound to THIS network specifically. Fails with EPERM on
                // captive (restricted) networks. Save and restore the prior
                // binding so other app I/O isn't disturbed.
                val previousBinding = connectivityManager.boundNetworkForProcess
                val bindResult = try {
                    connectivityManager.bindProcessToNetwork(network)
                    probe.probe(network, testUrl = probeUrl)
                } finally {
                    connectivityManager.bindProcessToNetwork(previousBinding)
                }

                when (bindResult) {
                    is ProbeResult.Portal -> {
                        Log.d(TAG, "Captive portal detected on $network (bind path)")
                        captive.add(network)
                        trySend(NetworkEvent.CaptiveNetworkAvailable(network, bindResult.locationUrl))
                        return@launch
                    }
                    is ProbeResult.Validated -> {
                        // Capability said NOT validated; probe said 204. The
                        // probe wins (capability bit was stale).
                        Log.d(TAG, "Network $network probed validated despite NOT_VALIDATED capability")
                        return@launch
                    }
                    is ProbeResult.Error ->
                        Log.w(TAG, "Bind probe error on $network: ${bindResult.message}; trying default route")
                }

                // PATH 2 — userspace fallback. probe(null) calls
                // URL.openConnection() directly, no bind, follows default
                // route. Works for users without an active VPN.
                val fallbackResult = probe.probe(network = null, testUrl = probeUrl)
                when (fallbackResult) {
                    is ProbeResult.Portal -> {
                        Log.d(TAG, "Captive portal detected on $network (default-route fallback)")
                        captive.add(network)
                        trySend(NetworkEvent.CaptiveNetworkAvailable(network, fallbackResult.locationUrl))
                    }
                    is ProbeResult.Validated, is ProbeResult.Error -> {
                        // Default route didn't see the redirect either. Build
                        // a diagnostics snapshot so the UI can guide the user.
                        val fallbackError = when (fallbackResult) {
                            is ProbeResult.Error -> fallbackResult.message
                            is ProbeResult.Validated ->
                                "default route returned 204 (probe went via a different network — likely VPN tunnel or cellular)"
                            else -> null
                        }
                        val defaultRouteBypassesCaptive =
                            fallbackResult is ProbeResult.Validated
                        val diagnostics = buildDiagnostics(
                            network = network,
                            bindError = bindResult.message,
                            fallbackError = fallbackError,
                            defaultRouteBypassesCaptive = defaultRouteBypassesCaptive,
                        )
                        Log.w(TAG, "Captive suspected on $network: $diagnostics")
                        trySend(NetworkEvent.CaptivePortalSuspected(network, diagnostics))
                        // Allow re-probing on the next capability change.
                        probed.remove(network)
                    }
                }
            }
        }

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                Log.d(TAG, "Network available: $network")
                // Don't probe yet — wait for capabilities. NetworkCallback
                // contract: onAvailable always followed by onCapabilitiesChanged.
            }

            override fun onCapabilitiesChanged(
                network: Network,
                caps: NetworkCapabilities,
            ) {
                val hasInternet = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                val isValidated = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
                val newSummary = hasInternet to isValidated
                val previousSummary = lastCaps.put(network, newSummary)
                if (previousSummary != newSummary) {
                    Log.d(
                        TAG,
                        "Capabilities for $network: internet=$hasInternet validated=$isValidated",
                    )
                }
                if (!hasInternet) return

                if (isValidated) {
                    // Validated. Two cases:
                    // 1. We previously identified this network as captive →
                    //    user just signed in. Surface NetworkValidated.
                    // 2. First-time validated observation → emit NoPortal.
                    if (captive.remove(network)) {
                        Log.i(TAG, "Captive network $network became validated — sign-in succeeded")
                        trySend(NetworkEvent.NetworkValidated(network))
                    } else if (reportedNoPortal.add(network)) {
                        Log.d(TAG, "Network $network observed validated, no portal")
                        trySend(NetworkEvent.NetworkObservedNoPortal(network))
                    }
                    probed.remove(network)
                    return
                }
                // INTERNET present but NOT validated → likely captive. Probe it.
                probeAndEmit(network)
            }

            override fun onLost(network: Network) {
                Log.d(TAG, "Network lost: $network")
                probed.remove(network)
                lastCaps.remove(network)
                reportedNoPortal.remove(network)
                // Only emit Lost for networks we previously identified as
                // captive. A regular WiFi disconnect with no portal in flight
                // is not a session event.
                if (captive.remove(network)) {
                    trySend(NetworkEvent.CaptiveNetworkLost(network))
                }
            }
        }

        connectivityManager.registerNetworkCallback(request, callback)
        Log.i(TAG, "CaptivePortalMonitor registered on TRANSPORT_WIFI + TRANSPORT_ETHERNET")

        awaitClose {
            Log.i(TAG, "CaptivePortalMonitor unregistering")
            connectivityManager.unregisterNetworkCallback(callback)
            // Cancel any in-flight probe coroutines so they don't outlive the flow.
            ioScope.cancel()
        }
    }

    /**
     * Re-snapshot the environment (VPN, Private DNS, proxy, DNS count,
     * cellular) for a network we already flagged as suspected-captive. Used by
     * the manual "Run diagnostics again" path so the user sees fresh state
     * after e.g. pausing their VPN. The probe errors are carried over from the
     * original failure — this method does not re-probe.
     */
    fun snapshotDiagnostics(
        network: Network,
        bindError: String?,
        fallbackError: String?,
        defaultRouteBypassesCaptive: Boolean,
    ): NetworkDiagnostics = buildDiagnostics(network, bindError, fallbackError, defaultRouteBypassesCaptive)

    /**
     * Snapshot the current network and global state for the troubleshooting
     * UI. Called when both probe paths fail. All field reads are wrapped in
     * runCatching because LinkProperties / VPN enumeration can race with
     * network teardown.
     */
    private fun buildDiagnostics(
        network: Network,
        bindError: String?,
        fallbackError: String?,
        defaultRouteBypassesCaptive: Boolean,
    ): NetworkDiagnostics {
        val linkProps = runCatching { connectivityManager.getLinkProperties(network) }.getOrNull()
        val vpn = runCatching { VpnDetector.detect() }.getOrNull()
        val proxy = linkProps?.httpProxy

        val httpProxyDescription: String? = when {
            proxy == null -> null
            proxy.pacFileUrl != null && proxy.pacFileUrl.toString().isNotEmpty() ->
                "PAC: ${proxy.pacFileUrl}"
            proxy.host.isNullOrEmpty() -> null
            else -> "${proxy.host}:${proxy.port.coerceAtLeast(0)}"
        }

        return NetworkDiagnostics(
            networkId = network.toString(),
            bindProbeError = bindError,
            fallbackProbeError = fallbackError,
            vpnInterfaces = vpn?.interfaces.orEmpty(),
            isTailscaleFullTunnel = vpn?.isTailscaleFullTunnel == true,
            privateDnsActive = linkProps?.isPrivateDnsActive == true,
            privateDnsServer = linkProps?.privateDnsServerName,
            httpProxyDescription = httpProxyDescription,
            dnsServerCount = linkProps?.dnsServers?.size ?: 0,
            hasValidatedCellular = hasValidatedCellular(),
            defaultRouteBypassesCaptive = defaultRouteBypassesCaptive,
        )
    }

    /**
     * `true` if any currently-known network is cellular AND validated.
     * `allNetworks` is deprecated in favor of callback tracking, but for a
     * one-shot diagnostic snapshot the simple enumeration is the right tool.
     */
    private fun hasValidatedCellular(): Boolean = runCatching {
        @Suppress("DEPRECATION")
        connectivityManager.allNetworks.any { net ->
            val caps = connectivityManager.getNetworkCapabilities(net) ?: return@any false
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        }
    }.getOrDefault(false)
}
