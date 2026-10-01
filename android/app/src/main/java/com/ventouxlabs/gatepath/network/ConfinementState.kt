package com.ventouxlabs.gatepath.network

import java.net.URI

/**
 * Is Gatepath's traffic confined to the captive Wi-Fi right now?
 *
 * Classified once per captive incident from the monitor's probe results.
 * In-app sign-in is offered only from [Confined]. Pure Kotlin: the decision
 * is the security-relevant part of the app, so it runs under the no-SDK
 * JVM suite.
 *
 * Why EPERM means "tunnelled": netd refuses explicit network selection for a
 * UID under a secure (non-bypassable) VPN unless the UID can protect sockets
 * (`NetworkController::checkUserNetworkAccessLocked`). Every VPN client we
 * care about is secure, so `bindProcessToNetwork(wifi)` fails with EPERM on
 * every connect while the VPN covers us. EACCES is the lockdown PROHIBIT rule.
 *
 * [classify] decides EPERM/EACCES from [ProbeErrorReason], not by matching
 * text in the probe's error message — see [probeErrorReason] for how the
 * errno is recovered from the exception's cause chain.
 */
sealed interface ConfinementState {
    val schemaName: String

    data class Confined(val portalUrl: String, val capture: PortalProbeCapture?) : ConfinementState {
        override val schemaName get() = "confined"
    }

    data class Tunnelled(val vpnKind: VpnKind) : ConfinementState {
        override val schemaName get() = "tunnelled"
    }

    data class Blocked(val vpnKind: VpnKind) : ConfinementState {
        override val schemaName get() = "blocked"
    }

    data class DnsStrict(val portalHost: String) : ConfinementState {
        override val schemaName get() = "dns_strict"
    }

    data class Unknown(
        val bindError: String?,
        val fallbackError: String?,
        val reason: UnknownReason,
    ) : ConfinementState {
        override val schemaName get() = "unknown"
    }
}

/** Everything [classify] needs; the monitor collects it, the ViewModel decides. */
data class ClassificationInputs(
    val bound: ProbeResult,
    val fallback: ProbeResult?,
    val vpnInterfaces: List<String>,
    val privateDnsStrict: Boolean,
    /** null when the portal URL has no hostname or no lookup was attempted. */
    val portalHostResolvedOnWifi: Boolean?,
    /**
     * The sign-in URL Android handed the system handoff, set only while
     * Android still flags the network `NET_CAPABILITY_CAPTIVE_PORTAL`. That
     * is Android's own verdict that this network is a portal, from a probe
     * of its own endpoint. Always null on the monitor path.
     */
    val systemPortalUrl: String? = null,
    /** The process-wide bind to this network was granted (the WebView is confined). */
    val processBindHeld: Boolean = false,
)

fun classify(inputs: ClassificationInputs): ConfinementState {
    val vpnKind = VpnKind.fromInterfaces(inputs.vpnInterfaces)
    return when (val bound = inputs.bound) {
        is ProbeResult.Portal -> confinedUnlessDnsStrict(bound.locationUrl, bound.capture, inputs)
        is ProbeResult.Error -> when (bound.reason) {
            // The fallback substring match lives entirely inside
            // probeErrorReason() (see ProbeErrorReason.kt) — an Error whose
            // message happens to contain "EPERM"/"EACCES" but whose derived
            // reason is OTHER falls through to Unknown here, not Tunnelled/
            // Blocked. classify() only ever looks at the typed reason.
            ProbeErrorReason.PERMISSION_DENIED -> ConfinementState.Tunnelled(vpnKind)
            ProbeErrorReason.ACCESS_BLOCKED -> ConfinementState.Blocked(vpnKind)
            ProbeErrorReason.CONNECTION_REFUSED,
            ProbeErrorReason.UNREACHABLE,
            ProbeErrorReason.TIMEOUT,
            ProbeErrorReason.OTHER,
            -> ConfinementState.Unknown(bound.message, fallbackMessage(inputs.fallback), UnknownReason.PROBE_ERROR)
        }
        // A 204 on our one probe endpoint is not proof there is no portal:
        // venue walled gardens let connectivitycheck.gstatic.com through
        // before sign-in. When Android's own probe (another endpoint) says
        // portal and handed us its URL, the 204 still proves our bound socket
        // reaches the network over the Wi-Fi, and the held process bind proves
        // the WebView will be confined too, so sign in on Android's URL.
        is ProbeResult.Validated -> {
            val systemUrl = inputs.systemPortalUrl
            if (systemUrl != null && inputs.processBindHeld) {
                confinedUnlessDnsStrict(systemUrl, capture = null, inputs)
            } else {
                ConfinementState.Unknown("bound probe returned 204", fallbackMessage(inputs.fallback), UnknownReason.BOUND_VALIDATED)
            }
        }
    }
}

/** [ConfinementState.Confined] on [url], or [ConfinementState.DnsStrict] if strict Private DNS cannot resolve its host. */
private fun confinedUnlessDnsStrict(url: String, capture: PortalProbeCapture?, inputs: ClassificationInputs): ConfinementState {
    val host = runCatching { URI(url).host }.getOrNull()
    val isHostname = host != null && !isIpLiteral(host)
    return if (isHostname && inputs.privateDnsStrict && inputs.portalHostResolvedOnWifi == false) {
        ConfinementState.DnsStrict(requireNotNull(host))
    } else {
        ConfinementState.Confined(url, capture)
    }
}

private fun fallbackMessage(fallback: ProbeResult?): String? = when (fallback) {
    is ProbeResult.Error -> fallback.message
    is ProbeResult.Validated -> "default route returned 204"
    is ProbeResult.Portal -> "default route saw the portal"
    null -> null
}

private fun isIpLiteral(host: String): Boolean =
    host.all { it.isDigit() || it == '.' } || host.contains(':')
