package com.ventouxlabs.gatepath.network

import android.util.Log
import java.net.NetworkInterface
import java.net.URL

private const val TAG = "GatepathVpnDetector"
private const val TAILSCALE_STATUS_URL = "http://100.100.100.100/localapi/v0/status"
private const val TAILSCALE_CONNECT_TIMEOUT_MS = 2_000
private const val TAILSCALE_READ_TIMEOUT_MS = 2_000

// Cap how much of the localapi status body is read into memory before parsing.
// Bounded by BYTES (matching the desktop detector's byte cap) and set well
// above any realistic /v0/status size — which scales with tailnet peer count —
// so a legitimate large response is never truncated; this only bounds a runaway
// or hostile local endpoint. Over-limit bodies fail safe to "no full tunnel",
// consistent with the rest of this best-effort detector.
private const val TAILSCALE_MAX_STATUS_BYTES = 8 * 1024 * 1024

/**
 * Best-effort VPN detection. All operations are wrapped in try/catch —
 * any failure is logged and treated as "no VPN detected".
 */
data class VpnInfo(
    /** Human-readable interface descriptors: "<iface> (<mode>)" */
    val interfaces: List<String>,
    /** True if Tailscale is active with a full-tunnel exit node. */
    val isTailscaleFullTunnel: Boolean,
)

object VpnDetector {

    /**
     * Enumerate active VPN-pattern network interfaces.
     * Returns a [VpnInfo] describing what was found.
     * Never throws — all errors are swallowed and logged.
     */
    fun detect(): VpnInfo {
        val detected = mutableListOf<String>()

        runCatching {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return@runCatching
            for (iface in ifaces.asSequence()) {
                if (!iface.isUp) continue
                if (VpnHeuristics.isVpnInterfaceName(iface.name)) {
                    detected.add(VpnHeuristics.describeVpnInterface(iface.name))
                }
            }
        }.onFailure { ex ->
            Log.w(TAG, "Interface enumeration failed: ${ex.message}")
        }

        val isFullTunnel = checkTailscaleFullTunnel()
        return VpnInfo(interfaces = detected, isTailscaleFullTunnel = isFullTunnel)
    }

    private fun checkTailscaleFullTunnel(): Boolean {
        return runCatching {
            val conn = URL(TAILSCALE_STATUS_URL).openConnection() as java.net.HttpURLConnection
            conn.connectTimeout = TAILSCALE_CONNECT_TIMEOUT_MS
            conn.readTimeout = TAILSCALE_READ_TIMEOUT_MS
            conn.requestMethod = "GET"
            try {
                conn.connect()
                val body = conn.inputStream.use {
                    BoundedReader.readBounded(it, TAILSCALE_MAX_STATUS_BYTES)
                }
                body != null && VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body)
            } finally {
                conn.disconnect()
            }
        }.getOrElse { ex ->
            Log.d(TAG, "Tailscale check skipped: ${ex.message}")
            false
        }
    }
}
