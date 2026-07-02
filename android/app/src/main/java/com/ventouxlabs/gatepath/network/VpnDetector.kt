package com.ventouxlabs.gatepath.network

import android.util.Log
import java.net.NetworkInterface
import java.net.URL

private const val TAG = "GatepathVpnDetector"
private const val TAILSCALE_STATUS_URL = "http://100.100.100.100/localapi/v0/status"
private const val TAILSCALE_CONNECT_TIMEOUT_MS = 2_000
private const val TAILSCALE_READ_TIMEOUT_MS = 2_000

/** VPN-related interface name prefixes that indicate an active tunnel. */
private val VPN_PREFIXES = listOf("tun", "tap", "wg", "ipsec", "ppp", "tailscale", "torguard")

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
                val name = iface.name.lowercase()
                val isVpn = VPN_PREFIXES.any { prefix -> name.startsWith(prefix) }
                if (isVpn) {
                    val mode = classifyMode(name)
                    detected.add("${iface.name} ($mode)")
                }
            }
        }.onFailure { ex ->
            Log.w(TAG, "Interface enumeration failed: ${ex.message}")
        }

        val isFullTunnel = checkTailscaleFullTunnel()
        return VpnInfo(interfaces = detected, isTailscaleFullTunnel = isFullTunnel)
    }

    private fun classifyMode(ifaceName: String): String = when {
        ifaceName.startsWith("tailscale") -> "split_tunnel" // overridden below if exit node
        else -> "unknown"
    }

    private fun checkTailscaleFullTunnel(): Boolean {
        return runCatching {
            val conn = URL(TAILSCALE_STATUS_URL).openConnection() as java.net.HttpURLConnection
            conn.connectTimeout = TAILSCALE_CONNECT_TIMEOUT_MS
            conn.readTimeout = TAILSCALE_READ_TIMEOUT_MS
            conn.requestMethod = "GET"
            try {
                conn.connect()
                val body = conn.inputStream.bufferedReader().readText()
                body.contains("\"ExitNodeID\"") && !body.contains("\"ExitNodeID\":\"\"")
            } finally {
                conn.disconnect()
            }
        }.getOrElse { ex ->
            Log.d(TAG, "Tailscale check skipped: ${ex.message}")
            false
        }
    }
}
