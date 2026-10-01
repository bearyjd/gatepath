package com.ventouxlabs.gatepath.network

import java.net.URI

/**
 * Is Gatepath's traffic confined to the captive Wi-Fi right now?
 *
 * Classified once per captive incident from the monitor's probe results, and
 * by the system handoff from its own bound probe plus Android's verdict.
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
     * The sign-in URL from the system handoff's intent
     * (`EXTRA_CAPTIVE_PORTAL_URL`). Untrusted on its own: the handoff activity
     * is exported, so any app can launch it with any URL. Only
     * [androidPortalVerdict] turns it into Android's verdict. Always null on
     * the monitor path.
     */
    val systemPortalUrl: String? = null,
    /**
     * Android currently flags the network `NET_CAPABILITY_CAPTIVE_PORTAL`:
     * system state, from Android's own probe of its own endpoint, which no
     * other app can fake.
     */
    val systemFlagsCaptive: Boolean = false,
    /**
     * The handoff's `acquire` returned a lease on this network. Not a guarantee
     * by itself (see ProcessBinding's borrow caveat); `GatepathWebView`
     * re-acquires its own lease and fails closed without one.
     */
    val processBindHeld: Boolean = false,
)

/**
 * Android's portal verdict for the system handoff: the intent's sign-in URL,
 * but only while Android still flags the network captive and only if it is a
 * plain http(s) URL with a host. Null otherwise. The activity loads this URL,
 * so a spoofed intent can at most supply a URL while a network really is
 * captive.
 */
fun androidPortalVerdict(intentUrl: String?, systemFlagsCaptive: Boolean): String? {
    if (intentUrl == null || !systemFlagsCaptive) return null
    val uri = runCatching { URI(intentUrl) }.getOrNull() ?: return null
    val scheme = uri.scheme?.lowercase()
    return intentUrl.takeIf { (scheme == "http" || scheme == "https") && !uri.host.isNullOrEmpty() }
}

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
            val androidUrl = androidPortalVerdict(inputs.systemPortalUrl, inputs.systemFlagsCaptive)
            if (androidUrl != null && inputs.processBindHeld) {
                confinedUnlessDnsStrict(androidUrl, capture = null, inputs)
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
