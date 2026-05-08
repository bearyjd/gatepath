package cc.grepon.gatepath.network

/**
 * Snapshot of why captive-portal sign-in is failing. Built when
 * [CaptivePortalMonitor] emits [NetworkEvent.CaptivePortalSuspected] and
 * surfaced to the UI via the troubleshooting panel.
 *
 * The fields are deliberately user-readable strings — this object gets
 * shown to a human, not parsed by another module.
 */
data class NetworkDiagnostics(
    /** `Network.toString()` (the netId — useful when comparing to logcat). */
    val networkId: String,

    /**
     * Error from the bind-then-probe path
     * (`network.openConnection()` after `bindProcessToNetwork(network)`).
     * Typically `EPERM` on captive networks because Android marks them
     * restricted. `null` if that path actually succeeded (rare).
     */
    val bindProbeError: String?,

    /**
     * Error from the userspace fallback (`URL.openConnection()` with no
     * bind, follows the kernel's default route). `null` if it succeeded —
     * but in that case we'd have emitted `CaptiveNetworkAvailable`, not
     * `CaptivePortalSuspected`, so this is `null` only for the
     * "fallback returned 204 from a different network (cellular/VPN)" case.
     */
    val fallbackProbeError: String?,

    /**
     * VPN interfaces detected by [VpnDetector]. Empty list = no VPN.
     * If non-empty, the userspace fallback's default route was almost
     * certainly the VPN tunnel — that's why it didn't see the captive
     * gateway.
     */
    val vpnInterfaces: List<String>,

    /**
     * `true` if Tailscale has an active exit node — the most reliable
     * "your default route is hijacked" signal we can detect.
     */
    val isTailscaleFullTunnel: Boolean,

    /**
     * Android system "Private DNS" (DoT/DoH) is configured. Captive portals
     * commonly intercept DNS, which breaks Private DNS until sign-in.
     * Detected via [android.net.LinkProperties.isPrivateDnsActive].
     */
    val privateDnsActive: Boolean,

    /**
     * Hostname for strict-mode Private DNS, or `null` if Private DNS is in
     * Auto / Off mode. Available API 28+, our minSdk is 29.
     */
    val privateDnsServer: String?,

    /**
     * Per-network HTTP proxy configured in Wi-Fi settings. Most captive
     * portals don't honor proxy settings; a misconfigured proxy can make
     * sign-in unreachable.
     */
    val httpProxyDescription: String?,

    /**
     * Number of DNS servers the network advertised. Zero means DHCP gave
     * us no DNS at all — usually only happens during a half-broken connect.
     */
    val dnsServerCount: Int,
)
