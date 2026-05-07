package cc.grepon.gatepath.network

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
}

/**
 * Wraps [ConnectivityManager.NetworkCallback] in a cold [Flow].
 *
 * Listens for any WiFi or Ethernet network with INTERNET capability and detects
 * captive portals via [PortalProbe].
 *
 * Captive detection rule (per the original spec): a network is treated as
 * captive when it has `NET_CAPABILITY_INTERNET` but lacks
 * `NET_CAPABILITY_VALIDATED`. We confirm with a probe and only emit
 * [NetworkEvent.CaptiveNetworkAvailable] when the probe returns
 * [ProbeResult.Portal].
 *
 * Lifecycle:
 * - Captive detected → emit `CaptiveNetworkAvailable`
 * - Captive then validated → emit `NetworkValidated` (success: user signed in)
 * - Captive then lost → emit `CaptiveNetworkLost`
 * - Validated network transitions are silent (no event for non-captive lifecycle)
 *
 * Collect this flow from a lifecycle-scoped coroutine (e.g. viewModelScope).
 */
class CaptivePortalMonitor(
    private val connectivityManager: ConnectivityManager,
    private val probe: PortalProbe = PortalProbe(),
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
                Log.d(TAG, "Probing network $network")
                when (val result = probe.probe(network)) {
                    is ProbeResult.Portal -> {
                        Log.d(TAG, "Captive portal detected on $network")
                        captive.add(network)
                        trySend(NetworkEvent.CaptiveNetworkAvailable(network, result.locationUrl))
                    }
                    is ProbeResult.Validated ->
                        Log.d(TAG, "Network $network validated (no portal)")
                    is ProbeResult.Error -> {
                        Log.w(TAG, "Probe error on $network: ${result.message}")
                        // Allow re-probing on the next capability change after an error.
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
                    //    user just signed in. Surface NetworkValidated so the
                    //    session transitions to Completed(PORTAL_COMPLETED).
                    // 2. First-time validated observation (the common case for
                    //    home WiFi) → emit NetworkObservedNoPortal so the UI
                    //    can say "no captive portal here" instead of looking
                    //    stuck at "Monitoring…".
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
}
