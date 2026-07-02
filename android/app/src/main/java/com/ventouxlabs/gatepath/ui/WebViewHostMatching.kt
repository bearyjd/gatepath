package com.ventouxlabs.gatepath.ui

/**
 * Pure, JVM-testable host-matching for [GatepathWebView].
 *
 * Extracted from `GatepathWebView.shouldOverrideUrlLoading` so the same-origin
 * detection rule — used to decide whether a navigation is **same-origin** or
 * **off-domain (observed + counted in the audit log, but allowed to load for
 * captive-vendor compatibility)** — can be regression-tested without an
 * instrumented Android runtime. See [GatepathWebView] and `SECURITY_MODEL.md`
 * for why off-domain navigations are no longer hard-blocked.
 *
 * Match rule:
 *  - exact host match (case-insensitive, trailing-dot-tolerant), OR
 *  - [requestHost] is a subdomain of [portalHost]
 *
 * Defensive cases:
 *  - blank [portalHost] → always false. We treat every host as off-domain when
 *    we couldn't parse a portal host out of the redirect URL — better to count
 *    every navigation as off-domain (audit-log noise) than to accidentally
 *    treat every request as same-origin (audit-log blindness).
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
