package com.ventouxlabs.gatepath.ui

import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.UnknownReason
import com.ventouxlabs.gatepath.network.VpnKind

enum class ConfinementAction { SIGN_IN_HERE, OPEN_VPN_APP, OPEN_NETWORK_SETTINGS, SHARE_EVIDENCE }

/**
 * The one sentence and one action per [ConfinementState]. Pure Kotlin so the
 * copy is regression-tested like [PortalLoadErrorText]. Never carries a URL:
 * portal URLs embed MAC addresses and session tokens.
 */
object ConfinementStateText {

    fun sentence(state: ConfinementState, vpnAppLabel: String?): String = when (state) {
        is ConfinementState.Confined ->
            "Gatepath is confined to this Wi-Fi. You can sign in here."
        is ConfinementState.Tunnelled ->
            "Your VPN is carrying Gatepath's traffic. Exclude Gatepath in " +
                "${vpnAppLabel?.takeUnless { it.isBlank() } ?: vpnFallbackLabel(state.vpnKind)} to sign in here, " +
                "or use the system notification."
        is ConfinementState.Blocked ->
            "Your VPN's kill switch is blocking Gatepath. Exclude Gatepath in " +
                "${vpnAppLabel?.takeUnless { it.isBlank() } ?: vpnFallbackLabel(state.vpnKind)} or use the system notification."
        is ConfinementState.DnsStrict ->
            "Private DNS is strict, so ${state.portalHost} cannot be resolved on this Wi-Fi. " +
                "Set Private DNS to Automatic for this sign-in, or use the system notification."
        is ConfinementState.Unknown ->
            "Gatepath could not work out what this network is doing. Share the evidence."
    }

    fun action(state: ConfinementState): ConfinementAction = when (state) {
        is ConfinementState.Confined -> ConfinementAction.SIGN_IN_HERE
        is ConfinementState.Tunnelled, is ConfinementState.Blocked -> ConfinementAction.OPEN_VPN_APP
        is ConfinementState.DnsStrict -> ConfinementAction.OPEN_NETWORK_SETTINGS
        is ConfinementState.Unknown -> ConfinementAction.SHARE_EVIDENCE
    }

    fun actionLabel(action: ConfinementAction): String = when (action) {
        ConfinementAction.SIGN_IN_HERE -> "Sign in here"
        ConfinementAction.OPEN_VPN_APP -> "Open VPN app"
        ConfinementAction.OPEN_NETWORK_SETTINGS -> "Open network settings"
        ConfinementAction.SHARE_EVIDENCE -> "Share evidence"
    }

    fun vpnFallbackLabel(kind: VpnKind): String = when (kind) {
        VpnKind.TAILSCALE -> "Tailscale"
        VpnKind.TORGUARD -> "TorGuard"
        VpnKind.OTHER, VpnKind.NONE -> "your VPN app"
    }

    /**
     * What the system-handoff entry point (`CaptivePortalActivity`) offers for
     * [ConfinementState.Unknown]. That screen owns no evidence bundle, so the
     * default Unknown copy ("Share the evidence.") would name a control it
     * cannot show.
     *
     * [reason] is [UnknownReason.BOUND_VALIDATED] when the *socket-scoped*
     * probe reached the gateway and got a 204. That proves the probe's own
     * socket routed correctly — it does **not** prove
     * `ConnectivityManager.bindProcessToNetwork` itself took, and under a
     * secure VPN the two can disagree: a probe can validate over one path
     * while the process-wide bind that the WebView actually depends on was
     * refused or silently ignored. A validated Wi-Fi probe alongside a system
     * captive-portal token is itself an odd combination — the system's own
     * probe may have travelled the VPN rather than the Wi-Fi link — which is
     * exactly why [processBindHeld] (`lease != null` at the call site, not
     * the probe result) gates the sign-in offer rather than [reason] alone.
     *
     * - [UnknownReason.BOUND_VALIDATED] with [processBindHeld] true, or no
     *   VPN interface up at all ([vpnKind] is `NONE`, where the distinction
     *   is moot): the WebView will actually load over the Wi-Fi network, so
     *   this gets the sign-in offer.
     * - [UnknownReason.BOUND_VALIDATED] with [processBindHeld] false and a
     *   VPN interface up: the probe validated but the process bind did not,
     *   so the WebView would load over the VPN's default route instead —
     *   the exact leak this feature exists to prevent. Offer the VPN app,
     *   same as [ConfinementState.Tunnelled].
     * - [UnknownReason.PROBE_ERROR] and no VPN: the system's own probe saw a
     *   portal (that is why the handoff happened), which outranks our
     *   inconclusive one, so the honest offer is to try the sign-in page
     *   anyway.
     * - [UnknownReason.PROBE_ERROR] and a VPN interface is up: an
     *   inconclusive bound probe under a VPN is far more likely a tunnelled
     *   bind the platform did not surface as a typed errno than a real
     *   portal, so the useful next step is the same as
     *   [ConfinementState.Tunnelled]'s — exclude Gatepath in the VPN app.
     */
    fun handoffUnknown(
        reason: UnknownReason,
        vpnKind: VpnKind,
        vpnAppLabel: String?,
        processBindHeld: Boolean,
    ): HandoffUnknownCopy {
        val offerSignIn = when (reason) {
            UnknownReason.BOUND_VALIDATED -> processBindHeld || vpnKind == VpnKind.NONE
            UnknownReason.PROBE_ERROR -> vpnKind == VpnKind.NONE
        }
        return if (offerSignIn) {
            HandoffUnknownCopy(
                sentence = "Gatepath could not work out what this network is doing.",
                action = ConfinementAction.SIGN_IN_HERE,
                actionLabel = "Try signing in anyway",
            )
        } else {
            HandoffUnknownCopy(
                sentence = "Gatepath could not work out what this network is doing, and a VPN is active. " +
                    "Exclude Gatepath in ${vpnAppLabel?.takeUnless { it.isBlank() } ?: vpnFallbackLabel(vpnKind)} " +
                    "and try again, or use the system notification.",
                action = ConfinementAction.OPEN_VPN_APP,
                actionLabel = actionLabel(ConfinementAction.OPEN_VPN_APP),
            )
        }
    }
}

/** The handoff entry point's rendering of an Unknown state; see [ConfinementStateText.handoffUnknown]. */
data class HandoffUnknownCopy(val sentence: String, val action: ConfinementAction, val actionLabel: String)
