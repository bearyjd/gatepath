package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.ui.SslErrorPolicy.shouldProceed
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression tests for the certificate-error bypass scope.
 *
 * `GatepathWebView.onReceivedSslError` proceeds past TLS errors so the
 * gateway's own login page (self-signed / expired / RFC1918-CN cert) renders
 * instead of silently white-screening. The security property being pinned here
 * is that the bypass stops at the portal host: because off-domain navigation is
 * deliberately unblocked for captive-vendor compatibility, an unscoped
 * `handler.proceed()` would disable certificate validation for any host the
 * gateway chooses to redirect to — on precisely the network where an attacker
 * is on-path by definition.
 */
class SslErrorPolicyTest {

    @Test
    fun `portal host itself proceeds`() {
        assertTrue(shouldProceed("192.168.1.1", "192.168.1.1"))
        assertTrue(shouldProceed("login.airport.net", "login.airport.net"))
    }

    @Test
    fun `subdomain of portal host proceeds`() {
        // Vendors split splash and grant across sub-hosts of the same domain.
        assertTrue(shouldProceed("auth.portal.airport.net", "portal.airport.net"))
    }

    @Test
    fun `unrelated host is cancelled - the point of the scope`() {
        // A hostile gateway redirecting to an identity provider and presenting
        // an untrusted cert must NOT be proceeded past.
        assertFalse(shouldProceed("accounts.google.com", "192.168.1.1"))
        assertFalse(shouldProceed("bank.example.com", "portal.airport.net"))
    }

    @Test
    fun `lookalike host is cancelled`() {
        assertFalse(shouldProceed("evil-airport.net", "airport.net"))
        assertFalse(shouldProceed("notairport.net", "airport.net"))
    }

    @Test
    fun `unparseable or blank hosts fail closed`() {
        // GatepathWebView passes "" when it can't parse a host out of either
        // URL. Unknown host must mean "enforce TLS", never "bypass TLS".
        assertFalse(shouldProceed("", "portal.airport.net"))
        assertFalse(shouldProceed("portal.airport.net", ""))
        assertFalse(shouldProceed("", ""))
    }

    @Test
    fun `host comparison is case and trailing-dot insensitive`() {
        assertTrue(shouldProceed("Portal.Airport.NET", "portal.airport.net"))
        assertTrue(shouldProceed("portal.airport.net.", "portal.airport.net"))
    }
}
