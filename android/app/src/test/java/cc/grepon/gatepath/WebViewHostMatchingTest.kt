package cc.grepon.gatepath

import cc.grepon.gatepath.ui.WebViewHostMatching.isSameOriginHost
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression tests for the WebView same-origin host-matching rule.
 *
 * This is the security boundary inside [cc.grepon.gatepath.ui.GatepathWebView]
 * that decides whether a captive-portal page is allowed to navigate or whether
 * the navigation gets blocked and counted. Pinning behavior on the JVM means
 * we don't have to wait until a regression makes it onto a phone.
 */
class WebViewHostMatchingTest {

    @Test
    fun `exact host match is same origin`() {
        assertTrue(isSameOriginHost("example.com", "example.com"))
        assertTrue(isSameOriginHost("portal.airport.net", "portal.airport.net"))
    }

    @Test
    fun `subdomain of portal host is same origin`() {
        assertTrue(isSameOriginHost("login.example.com", "example.com"))
        assertTrue(isSameOriginHost("a.b.example.com", "example.com"))
        assertTrue(isSameOriginHost("auth.portal.airport.net", "portal.airport.net"))
    }

    @Test
    fun `unrelated host is not same origin`() {
        assertFalse(isSameOriginHost("attacker.com", "example.com"))
        assertFalse(isSameOriginHost("portal.airport.net", "example.com"))
    }

    @Test
    fun `lookalike host is not same origin`() {
        // Classic prefix-confusion: ensure we use dot-boundary, not raw suffix.
        assertFalse(isSameOriginHost("evil-example.com", "example.com"))
        assertFalse(isSameOriginHost("notexample.com", "example.com"))
        assertFalse(isSameOriginHost("xexample.com", "example.com"))
    }

    @Test
    fun `host comparison is case insensitive`() {
        assertTrue(isSameOriginHost("EXAMPLE.com", "example.com"))
        assertTrue(isSameOriginHost("Login.Example.COM", "example.com"))
        assertTrue(isSameOriginHost("example.com", "EXAMPLE.COM"))
    }

    @Test
    fun `trailing-dot FQDN is normalized on either side`() {
        assertTrue(isSameOriginHost("example.com.", "example.com"))
        assertTrue(isSameOriginHost("example.com", "example.com."))
        assertTrue(isSameOriginHost("login.example.com.", "example.com"))
    }

    @Test
    fun `blank portal host blocks every request - defensive`() {
        // If we couldn't parse a portal host out of the redirect URL, refuse
        // every request rather than risk allowing arbitrary navigation.
        assertFalse(isSameOriginHost("anything.com", ""))
        assertFalse(isSameOriginHost("example.com", ""))
        // Pin the would-have-been bug: requestHost.endsWith(".") with empty
        // portalHost would have matched any trailing-dot FQDN.
        assertFalse(isSameOriginHost("example.com.", ""))
        assertFalse(isSameOriginHost(".", ""))
    }

    @Test
    fun `blank request host is not same origin`() {
        assertFalse(isSameOriginHost("", "example.com"))
        assertFalse(isSameOriginHost("   ", "example.com"))
    }

    @Test
    fun `whitespace around hosts is tolerated`() {
        assertTrue(isSameOriginHost(" example.com ", "example.com"))
        assertTrue(isSameOriginHost("example.com", " example.com "))
    }
}
