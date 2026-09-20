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

    data class Unknown(val bindError: String?, val fallbackError: String?) : ConfinementState {
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
)

fun classify(inputs: ClassificationInputs): ConfinementState {
    val vpnKind = VpnKind.fromInterfaces(inputs.vpnInterfaces)
    return when (val bound = inputs.bound) {
        is ProbeResult.Portal -> {
            val host = runCatching { URI(bound.locationUrl).host }.getOrNull()
            val isHostname = host != null && !isIpLiteral(host)
            if (isHostname && inputs.privateDnsStrict && inputs.portalHostResolvedOnWifi == false) {
                ConfinementState.DnsStrict(requireNotNull(host))
            } else {
                ConfinementState.Confined(bound.locationUrl, bound.capture)
            }
        }
        is ProbeResult.Error -> when {
            bound.message.contains("EPERM") -> ConfinementState.Tunnelled(vpnKind)
            bound.message.contains("EACCES") -> ConfinementState.Blocked(vpnKind)
            else -> ConfinementState.Unknown(bound.message, fallbackMessage(inputs.fallback))
        }
        is ProbeResult.Validated -> ConfinementState.Unknown("bound probe returned 204", fallbackMessage(inputs.fallback))
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
