package cc.grepon.gatepath.ui

/**
 * Pure, JVM-testable host-matching for [GatepathWebView].
 *
 * Extracted from `GatepathWebView.shouldOverrideUrlLoading` so the same-origin
 * rule — the actual security boundary that decides whether a navigation stays
 * inside the captive-portal page or is blocked — can be regression-tested
 * without an instrumented Android runtime.
 *
 * Match rule:
 *  - exact host match (case-insensitive, trailing-dot-tolerant), OR
 *  - [requestHost] is a subdomain of [portalHost]
 *
 * Defensive cases:
 *  - blank [portalHost] → always false. We refuse to allow navigation when we
 *    couldn't parse a portal host out of the redirect URL — better to over-block
 *    than to accidentally treat every request as same-origin.
 *  - blank [requestHost] → false.
 *
 * Pinned bug (would-have-been): naive `requestHost.endsWith(".$portalHost")`
 * with empty [portalHost] becomes `endsWith(".")`, which matches any FQDN
 * written with a trailing dot. The blank-portal guard prevents this.
 */
object WebViewHostMatching {

    fun isSameOriginHost(requestHost: String, portalHost: String): Boolean {
        val portal = portalHost.normalizeHost()
        val request = requestHost.normalizeHost()
        if (portal.isEmpty() || request.isEmpty()) return false
        if (request == portal) return true
        return request.endsWith(".$portal")
    }

    private fun String.normalizeHost(): String =
        trim().trimEnd('.').lowercase()
}
