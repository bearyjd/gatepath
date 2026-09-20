package com.ventouxlabs.gatepath.network

/**
 * Snapshot of the network environment at one captive incident. Built when
 * [CaptivePortalMonitor] emits [NetworkEvent.CaptiveIncident] and consumed by
 * the diagnostic engine and the incident evidence record.
 *
 * The fields are deliberately user-readable strings — this object gets
 * shown to a human, not parsed by another module.
 */
data class NetworkDiagnostics(
    /** `Network.toString()` (the netId — useful when comparing to logcat). */
    val networkId: String,

    /**
     * `EPERM` when a secure VPN covers Gatepath's UID; `EACCES` under
     * always-on lockdown; `null` when the bound probe reached the gateway.
     */
    val bindProbeError: String?,

    /**
     * Error from the userspace fallback (`URL.openConnection()` with no
     * bind, follows the kernel's default route). `null` when that path
     * returned a response rather than failing — either a 204 from a
     * different network (cellular/VPN) or the portal redirect itself.
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

    /**
     * `true` if a different network was cellular AND validated when this
     * snapshot was taken — mobile data silently carrying traffic can mask
     * the captive WiFi state entirely.
     */
    val hasValidatedCellular: Boolean,

    /**
     * `true` when the userspace fallback probe returned 204 — i.e. the
     * device's default route reaches the internet without passing through
     * the captive gateway (VPN tunnel or cellular). Diagnostic probes that
     * need to interrogate the captive network itself cannot do so in this
     * state, and say so rather than reporting a result for the wrong path.
     */
    val defaultRouteBypassesCaptive: Boolean,
)
