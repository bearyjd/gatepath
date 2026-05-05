package cc.grepon.gatepath

import cc.grepon.gatepath.network.BlockedDomains
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM tests for [BlockedDomains]. No Android SDK required.
 */
class BlockedDomainsTest {

    @Test
    fun `exact match is blocked`() {
        assertTrue(BlockedDomains.isBlocked("google-analytics.com"))
        assertTrue(BlockedDomains.isBlocked("hotjar.com"))
        assertTrue(BlockedDomains.isBlocked("amplitude.com"))
    }

    @Test
    fun `subdomain suffix match is blocked`() {
        assertTrue(BlockedDomains.isBlocked("www.google-analytics.com"))
        assertTrue(BlockedDomains.isBlocked("cdn.segment.com"))
        assertTrue(BlockedDomains.isBlocked("api.mixpanel.com"))
        assertTrue(BlockedDomains.isBlocked("tag.googletagmanager.com"))
    }

    @Test
    fun `unrelated domain is not blocked`() {
        assertFalse(BlockedDomains.isBlocked("example.com"))
        assertFalse(BlockedDomains.isBlocked("wifi.airport.net"))
        assertFalse(BlockedDomains.isBlocked("captive.portal.local"))
    }

    @Test
    fun `partial prefix match is not blocked`() {
        // "notgoogle-analytics.com" should NOT be blocked — it's not a suffix of google-analytics.com
        assertFalse(BlockedDomains.isBlocked("notgoogle-analytics.com"))
        assertFalse(BlockedDomains.isBlocked("myfacebook.com"))
    }

    @Test
    fun `matching is case sensitive`() {
        // BlockedDomains stores lowercase; callers must normalise — verify raw uppercase is NOT matched
        assertFalse(BlockedDomains.isBlocked("Google-Analytics.com"))
        assertFalse(BlockedDomains.isBlocked("HOTJAR.COM"))
    }

    @Test
    fun `all required domains are present`() {
        val required = setOf(
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
        assertTrue(
            "Missing domains: ${required - BlockedDomains.domains}",
            BlockedDomains.domains.containsAll(required),
        )
    }
}
