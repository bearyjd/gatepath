package cc.grepon.gatepath.diag

/**
 * Action the engine recommends in response to a [DiagnosticReport].
 *
 * Per D1 (confirmed 2026-05-08), Gatepath never auto-applies fixes — the user
 * must tap. Therefore this type is a *descriptor*, not a callable: the engine
 * returns a string [id] + user-visible [instruction], and the UI layer is
 * responsible for translating the id into an `Intent` (e.g. open Settings →
 * Network → Private DNS).
 *
 * Keeping the engine pure (no Android Intent references) means it stays
 * JVM-testable and the action catalog is one place to audit.
 */
sealed interface RecommendedAction {

    /** No actionable next step from this engine run. UI shows the static fallback list. */
    data object NoActionAvailable : RecommendedAction

    /**
     * A step the user must take in system Settings or another app. The UI
     * translates [id] into an `Intent` and shows [instruction] as the prompt.
     */
    data class UserAction(
        val id: String,
        val instruction: String,
    ) : RecommendedAction

    companion object Ids {
        const val OPEN_PRIVATE_DNS_SETTINGS = "open_private_dns_settings"
        const val PAUSE_VPN = "pause_vpn"
        const val DISABLE_HTTP_PROXY = "disable_http_proxy"
        const val USE_SYSTEM_HANDOFF = "use_system_handoff"
        const val DISABLE_CELLULAR = "disable_cellular_temporarily"
        const val APPLY_WEBVIEW_BRIDGE = "apply_webview_bridge"   // Phase 3.5
    }
}
