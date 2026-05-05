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
    /** A network with CAPTIVE_PORTAL capability became available at [portalUrl]. */
    data class CaptiveNetworkAvailable(
        val network: Network,
        val portalUrl: String,
    ) : NetworkEvent

    /** The captive network was lost before the session completed. */
    data class CaptiveNetworkLost(val network: Network) : NetworkEvent
}

/**
 * Wraps [ConnectivityManager.NetworkCallback] in a cold [Flow] and probes each
 * new captive network for a portal redirect URL.
 *
 * Collect this flow from a lifecycle-scoped coroutine (e.g. viewModelScope).
 */
class CaptivePortalMonitor(
    private val connectivityManager: ConnectivityManager,
    private val probe: PortalProbe = PortalProbe(),
) {

    fun observe(): Flow<NetworkEvent> = callbackFlow {
        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_CAPTIVE_PORTAL)
            .build()

        val ioScope = CoroutineScope(Dispatchers.IO)

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                Log.d(TAG, "Captive network available: $network")
                ioScope.launch {
                    when (val result = probe.probe(network)) {
                        is ProbeResult.Portal ->
                            trySend(NetworkEvent.CaptiveNetworkAvailable(network, result.locationUrl))
                        is ProbeResult.Validated ->
                            Log.d(TAG, "Network $network validated (no portal)")
                        is ProbeResult.Error ->
                            Log.w(TAG, "Probe error on $network: ${result.message}")
                    }
                }
            }

            override fun onLost(network: Network) {
                Log.d(TAG, "Captive network lost: $network")
                trySend(NetworkEvent.CaptiveNetworkLost(network))
            }
        }

        connectivityManager.registerNetworkCallback(request, callback)

        awaitClose {
            connectivityManager.unregisterNetworkCallback(callback)
        }
    }
}
