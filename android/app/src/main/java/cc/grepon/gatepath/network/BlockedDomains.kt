package cc.grepon.gatepath.network

/**
 * Pure-Kotlin object listing tracker/analytics domains to block in the portal WebView.
 * Testable on plain JVM without Android SDK.
 */
object BlockedDomains {

    val domains: Set<String> = setOf(
        "google-analytics.com",
        "googletagmanager.com",
        "doubleclick.net",
        "facebook.com",
        "facebook.net",
        "analytics.yahoo.com",
        "hotjar.com",
        "segment.com",
        "mixpanel.com",
        "amplitude.com",
    )

    /**
     * Returns true if [host] exactly matches a blocked domain or is a subdomain of one.
     * Matching is case-sensitive (callers should normalise to lowercase before calling).
     */
    fun isBlocked(host: String): Boolean {
        if (domains.contains(host)) return true
        return domains.any { blocked -> host.endsWith(".$blocked") }
    }
}
