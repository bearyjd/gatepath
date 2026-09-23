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
    /**
     * Every captive-looking network produces exactly one of these per
     * evaluation. The ViewModel classifies it; the monitor does not decide.
     */
    data class CaptiveIncident(
        val network: Network,
        val inputs: ClassificationInputs,
        val boundPath: ProbePath,
        val diagnostics: NetworkDiagnostics,
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
 * Captive detection rule (per the original spec): a network is treated as
 * captive when it has `NET_CAPABILITY_INTERNET` but lacks
 * `NET_CAPABILITY_VALIDATED`. We confirm with two probe paths:
 *
 *   1. **Bind path** — `bindProcessToNetwork(network)` then
 *      `network.openConnection()`. Most authoritative. Fails with `EPERM`
 *      when a secure VPN covers this UID (netd `checkUserNetworkAccess`);
 *      see [ConfinementState].
 *
 *   2. **Userspace fallback** — `URL.openConnection()` with no bind. Routes
 *      via the kernel's default route. Works when there's no VPN
 *      intercepting the default; fails when VPN is up because the tunnel
 *      can't reach the captive gateway.
 *
 * The monitor does not decide what either result means. It packages both
 * probe outcomes plus the environment into [ClassificationInputs] and emits
 * one [NetworkEvent.CaptiveIncident]; [classify] turns that into a
 * [ConfinementState] in the ViewModel.
 *
 * Lifecycle:
 * - Captive-looking network probed → emit `CaptiveIncident` (exactly one per evaluation)
 * - Captive then validated → emit `NetworkValidated` (success: user signed in)
 * - Captive then lost → emit `CaptiveNetworkLost`
 * - Validated network transitions are silent (no event for non-captive lifecycle)
 *
 * Collect this flow from a lifecycle-scoped coroutine (e.g. viewModelScope).
 */
class CaptivePortalMonitor(
    private val connectivityManager: ConnectivityManager,
    private val processBinding: AndroidProcessBinding,
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
        // Networks whose bound probe actually found the portal. Used to gate
        // both NetworkValidated (success signal) and CaptiveNetworkLost
        // (don't emit Lost for non-captive networks the caller never cared about).
        val captive = java.util.concurrent.ConcurrentHashMap.newKeySet<Network>()
        // Every network that produced a CaptiveIncident, whatever it classified
        // as. `captive` holds only the bound-Portal ones, so gating
        // CaptiveNetworkLost on it would leave a Tunnelled/Blocked/DnsStrict
        // card on screen after the Wi-Fi went away.
        val incidents = java.util.concurrent.ConcurrentHashMap.newKeySet<Network>()
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
                val bindResult = processBinding.borrow(network) {
                    probe.probe(network, testUrl = probeUrl)
                }
                if (bindResult is ProbeResult.Validated) {
                    // Capability said NOT validated; the Wi-Fi itself answered 204. Not an incident.
                    Log.d(TAG, "Network $network probed validated despite NOT_VALIDATED capability")
                    return@launch
                }
                // The default-route probe is evidence, never the decision: it
                // shows whether some other path (VPN, cellular) has internet.
                // Skipped entirely when the bound probe already found the
                // portal — the answer cannot change the classification, and it
                // would delay the sign-in window by up to ten seconds of probe
                // timeouts on the one path where the user is waiting for it.
                // `classify` accepts a null fallback.
                val fallbackResult = if (bindResult is ProbeResult.Portal) {
                    null
                } else {
                    probe.probe(network = null, testUrl = probeUrl)
                }
                val linkProps = runCatching { connectivityManager.getLinkProperties(network) }.getOrNull()
                val privateDnsStrict = linkProps?.privateDnsServerName != null
                val resolved = (bindResult as? ProbeResult.Portal)?.let { resolvePortalHostOnWifi(network, it.locationUrl) }
                val diagnostics = buildDiagnostics(
                    network = network,
                    bindError = (bindResult as? ProbeResult.Error)?.message,
                    fallbackError = (fallbackResult as? ProbeResult.Error)?.message,
                    // Null when the fallback was skipped (only happens when
                    // the bound probe already found the portal) — "not
                    // measured", not a guessed false. The probes that gate on
                    // this decline (report Inconclusive) on null rather than
                    // assuming the default route does or doesn't bypass the
                    // captive network.
                    defaultRouteBypassesCaptive = fallbackResult?.let { it is ProbeResult.Validated },
                )
                val inputs = ClassificationInputs(
                    bound = bindResult,
                    fallback = fallbackResult,
                    vpnInterfaces = diagnostics.vpnInterfaces,
                    privateDnsStrict = privateDnsStrict,
                    portalHostResolvedOnWifi = resolved,
                )
                if (bindResult is ProbeResult.Portal) captive.add(network)
                incidents.add(network)
                Log.i(TAG, "Captive incident on $network: bound=${bindResult::class.simpleName}")
                trySend(NetworkEvent.CaptiveIncident(network, inputs, ProbePath.BOUND_WIFI, diagnostics))
                // Allow re-probing on the next capability change unless we found the portal.
                if (bindResult !is ProbeResult.Portal) probed.remove(network)
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
                    // The incident is over either way — leaving the network in
                    // `incidents` would fire a spurious CaptiveNetworkLost when
                    // the user later walks away from a network they signed into.
                    incidents.remove(network)
                    if (captive.remove(network)) {
                        // The bound probe had found a portal here, so reaching
                        // validated means the user got through it.
                        Log.i(TAG, "Captive network $network became validated — sign-in succeeded")
                        trySend(NetworkEvent.NetworkValidated(network))
                    } else if (reportedNoPortal.add(network)) {
                        // Includes a network that produced a non-Portal
                        // incident (Tunnelled, Blocked, DnsStrict, Unknown) and
                        // has now validated. Nobody signed in — the obstacle
                        // went away, typically because the VPN dropped — so
                        // this is "no portal", not "sign-in complete", and it
                        // must not complete a session as a portal success.
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
                captive.remove(network)
                // Emit Lost for any network that produced an incident, not just
                // the ones whose bound probe found the portal: a Tunnelled or
                // Blocked card describes a network too, and it must come down
                // when that network goes away. A regular Wi-Fi disconnect with
                // no incident behind it is still not a session event.
                if (incidents.remove(network)) {
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
     * Resolve the portal hostname through THIS network's resolver. Under strict
     * Private DNS the lookup goes to the DoT server, which the captive gateway
     * blocks, so failure here plus strict mode is the DnsStrict signal.
     */
    private fun resolvePortalHostOnWifi(network: Network, portalUrl: String): Boolean? {
        val host = runCatching { java.net.URI(portalUrl).host }.getOrNull() ?: return null
        if (host.all { it.isDigit() || it == '.' } || host.contains(':')) return null
        return runCatching { network.getAllByName(host).isNotEmpty() }.getOrDefault(false)
    }

    /**
     * Re-snapshot the environment (VPN, Private DNS, proxy, DNS count,
     * cellular) for a network we already flagged as captive. Used by
     * the manual "Run diagnostics again" path so the user sees fresh state
     * after e.g. pausing their VPN. The probe errors are carried over from the
     * original failure — this method does not re-probe.
     */
    fun snapshotDiagnostics(
        network: Network,
        bindError: String?,
        fallbackError: String?,
        defaultRouteBypassesCaptive: Boolean?,
    ): NetworkDiagnostics = buildDiagnostics(network, bindError, fallbackError, defaultRouteBypassesCaptive)

    /**
     * Snapshot the current network and global state for the incident record.
     * Called once per captive evaluation. All field reads are wrapped in
     * runCatching because LinkProperties / VPN enumeration can race with
     * network teardown.
     */
    private fun buildDiagnostics(
        network: Network,
        bindError: String?,
        fallbackError: String?,
        defaultRouteBypassesCaptive: Boolean?,
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
