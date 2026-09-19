package com.ventouxlabs.gatepath.ui

import com.ventouxlabs.gatepath.network.ConfinementState
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
                "${vpnAppLabel ?: vpnFallbackLabel(state.vpnKind)} to sign in here, " +
                "or use the system notification."
        is ConfinementState.Blocked ->
            "Your VPN's kill switch is blocking Gatepath. Exclude Gatepath in " +
                "${vpnAppLabel ?: vpnFallbackLabel(state.vpnKind)} or use the system notification."
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
}
