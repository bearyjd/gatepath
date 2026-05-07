package cc.grepon.gatepath.network

import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
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

    /** The captive network was lost before the session completed. */
    data class CaptiveNetworkLost(val network: Network) : NetworkEvent
}

/**
 * Wraps [ConnectivityManager.NetworkCallback] in a cold [Flow].
 *
 * Listens for any WiFi or Ethernet network with INTERNET capability. The
 * earlier implementation filtered on `NET_CAPABILITY_CAPTIVE_PORTAL`, which
 * is only set when the Android framework has already classified a network as
 * captive — for a normal WiFi connection it is never present, so the callback
 * never fired. The new filter receives every WiFi/Ethernet network and we
 * detect captive portals ourselves via [PortalProbe].
 *
 * Captive detection rule (per the original spec): a network is treated as
 * captive when it has `NET_CAPABILITY_INTERNET` but lacks
 * `NET_CAPABILITY_VALIDATED`. We confirm with a probe and only emit
 * [NetworkEvent.CaptiveNetworkAvailable] when the probe returns
 * [ProbeResult.Portal].
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
        // Track networks we've already probed (or have a probe in flight for)
        // so capability churn doesn't queue dozens of probes.
        val probed = java.util.concurrent.ConcurrentHashMap.newKeySet<Network>()

        fun probeAndEmit(network: Network) {
            if (!probed.add(network)) return
            ioScope.launch {
                Log.d(TAG, "Probing network $network")
                when (val result = probe.probe(network)) {
                    is ProbeResult.Portal -> {
                        Log.i(TAG, "Captive portal detected on $network at ${result.locationUrl}")
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
                Log.d(
                    TAG,
                    "Capabilities for $network: internet=$hasInternet validated=$isValidated",
                )
                if (!hasInternet) return

                if (isValidated) {
                    // Network is validated — no portal. If we previously emitted
                    // a captive event for this network, surface a "lost" so the
                    // session can transition to Completed.
                    if (probed.remove(network)) {
                        Log.d(TAG, "Network $network became validated; clearing")
                    }
                    return
                }
                // INTERNET present but NOT validated → likely captive. Probe it.
                probeAndEmit(network)
            }

            override fun onLost(network: Network) {
                Log.d(TAG, "Network lost: $network")
                probed.remove(network)
                trySend(NetworkEvent.CaptiveNetworkLost(network))
            }
        }

        connectivityManager.registerNetworkCallback(request, callback)
        Log.i(TAG, "CaptivePortalMonitor registered on TRANSPORT_WIFI + TRANSPORT_ETHERNET")

        awaitClose {
            Log.i(TAG, "CaptivePortalMonitor unregistering")
            connectivityManager.unregisterNetworkCallback(callback)
        }
    }
}
