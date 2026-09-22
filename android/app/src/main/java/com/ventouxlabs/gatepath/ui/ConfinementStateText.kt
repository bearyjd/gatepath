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
     * cannot show. Three cases instead:
     *
     * - [reason] is [UnknownReason.BOUND_VALIDATED]: the bound probe actually
     *   reached the gateway and got a 204, so the bind succeeded and the
     *   WebView will load regardless of any VPN interface — this always gets
     *   the sign-in offer, the same carve-out as the no-VPN case below.
     * - [reason] is [UnknownReason.PROBE_ERROR] and a VPN interface is up: an
     *   inconclusive bound probe under a VPN is far more likely a tunnelled
     *   bind the platform did not surface as a typed errno than a real
     *   portal, so the useful next step is the same as
     *   [ConfinementState.Tunnelled]'s — exclude Gatepath in the VPN app.
     * - [reason] is [UnknownReason.PROBE_ERROR] and no VPN: the system's own
     *   probe saw a portal (that is why the handoff happened), which
     *   outranks our inconclusive one, so the honest offer is to try the
     *   sign-in page anyway.
     */
    fun handoffUnknown(reason: UnknownReason, vpnKind: VpnKind, vpnAppLabel: String?): HandoffUnknownCopy =
        if (reason == UnknownReason.BOUND_VALIDATED || vpnKind == VpnKind.NONE) {
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

/** The handoff entry point's rendering of an Unknown state; see [ConfinementStateText.handoffUnknown]. */
data class HandoffUnknownCopy(val sentence: String, val action: ConfinementAction, val actionLabel: String)
