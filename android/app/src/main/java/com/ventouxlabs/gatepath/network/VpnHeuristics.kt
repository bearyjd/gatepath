package com.ventouxlabs.gatepath.network

/**
 * Pure, JVM-testable heuristics behind [VpnDetector].
 *
 * Kept free of android.* imports so the classification rules that decide
 * whether an interface counts as a VPN tunnel — a security-relevant signal
 * surfaced in the audit log — are covered by the plain-JVM test suite.
 */
object VpnHeuristics {

    /** VPN-related interface name prefixes that indicate an active tunnel. */
    val VPN_PREFIXES = listOf("tun", "tap", "wg", "ipsec", "ppp", "tailscale", "torguard")

    /** True if [name] (any case) matches a known VPN interface prefix. */
    fun isVpnInterfaceName(name: String): Boolean {
        val lower = name.lowercase()
        return VPN_PREFIXES.any { prefix -> lower.startsWith(prefix) }
    }

    /**
     * Human-readable descriptor for a detected VPN interface:
     * "<iface> (<mode>)". Mode is a coarse classification; Tailscale
     * defaults to split_tunnel and is upgraded to full-tunnel by the
     * localapi probe, all others are unknown.
     */
    fun describeVpnInterface(name: String): String = "$name (${classifyMode(name.lowercase())})"

    private fun classifyMode(ifaceName: String): String = when {
        ifaceName.startsWith("tailscale") -> "split_tunnel"
        else -> "unknown"
    }

    /**
     * True if a Tailscale localapi /v0/status response body indicates an
     * active full-tunnel exit node (non-empty ExitNodeID).
     */
    fun tailscaleBodyIndicatesFullTunnel(body: String): Boolean =
        body.contains("\"ExitNodeID\"") && !body.contains("\"ExitNodeID\":\"\"")
}
