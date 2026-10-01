package com.ventouxlabs.gatepath.diag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The sign-in log leaves the device (share sheet), and real portal URLs carry
 * device identifiers. Shapes below are from a real hotel portal (2026-09-30),
 * with the MAC, IP and ids replaced by fakes.
 */
class LogRedactionTest {

    private val portalRedirect =
        "https://secure.11os.com/portal/mikrotikgateway/?UI=JK-000-00_AA-BB-CC-DD-EE-FF_2" +
            "&MA=12%3A34%3A56%3A78%3A9A%3ABC&SIP=172.20.9.99"
    private val loyaltyHop =
        "https://www.marriott.com/en-us/einterface/v5/FRAWI?AT=CONNECT&LR=AF_AC&MA=123456789abc&Z=GUEST"

    // ── origin(): structural reduction for values known to be URLs ──────────

    @Test
    fun `origin keeps scheme, host and port only`() {
        assertEquals("https://secure.11os.com", LogRedaction.origin(portalRedirect))
        assertEquals("http://10.0.0.1:8080", LogRedaction.origin("http://10.0.0.1:8080/login?token=s3cret"))
        assertEquals("https://portal.example.net", LogRedaction.origin("https://guest:hunter2@portal.example.net/x"))
        assertEquals("http://[fe80::1]", LogRedaction.origin("http://[fe80::1]/login?mac=x"))
        assertEquals("intent://scan", LogRedaction.origin("intent://scan/#Intent;scheme=zxing;end"))
    }

    @Test
    fun `origin never passes a relative or broken url through`() {
        // A gateway's Location header may be relative (RFC 7231); the probe keeps it verbatim.
        assertEquals("(relative URL)", LogRedaction.origin("/login.php?MA=12%3A34&SIP=172.20.9.99&tok=s3cr3t"))
        assertEquals("(relative URL)", LogRedaction.origin("//portal.example.net/auth?mac=167ada6cf76a"))
        assertEquals("(none)", LogRedaction.origin(null))
        assertEquals("javascript:(no host)", LogRedaction.origin("javascript:alert(1)"))
        assertEquals("(unparseable URL)", LogRedaction.origin("https://bad host/?MA=1"))
    }

    // ── redact(): the free-text backstop ────────────────────────────────────

    @Test
    fun `urls inside free text are reduced and the rest of the text is kept`() {
        assertEquals(
            "Page started: https://www.marriott.com (after 2 hops)",
            LogRedaction.redact("Page started: $loyaltyHop (after 2 hops)"),
        )
        assertEquals("(see https://x.example.com)", LogRedaction.redact("(see https://x.example.com/a?b=1)"))
        assertEquals("go to https://Portal.Example.NET", LogRedaction.redact("go to HTTPS://Portal.Example.NET/x?y=1"))
    }

    @Test
    fun `credentials in a url never survive`() {
        assertEquals("loading https://portal.example.net", LogRedaction.redact("loading https://guest:hunter2@portal.example.net/login"))
    }

    @Test
    fun `a relative location in free text loses its query`() {
        val out = LogRedaction.redact("portal at /login.php?MA=12%3A34%3A56%3A78%3A9A%3ABC&SIP=172.20.9.99&tok=s3cr3t (HTTP 302)")
        for (secret in listOf("MA=", "SIP", "172.20", "s3cr3t", "%3A")) assertFalse("leaked '$secret' in: $out", out.contains(secret))
        assertTrue(out.contains("(HTTP 302)"))
    }

    @Test
    fun `an intent link with an encoded fallback url is reduced`() {
        val out = LogRedaction.redact(
            "Followed off-domain navigation to intent://scan/#Intent;S.browser_fallback_url=https%3A%2F%2Fx.example%2F%3FMA%3D1;end",
        )
        assertFalse(out.contains("browser_fallback"))
        assertFalse(out.contains("MA%3D"))
        assertTrue(out.contains("intent://scan"))
    }

    @Test
    fun `device ip addresses in error text are masked but a url host is kept`() {
        val out = LogRedaction.redact(
            "error TIMEOUT: failed to connect to connectivitycheck.gstatic.com/142.251.13.94 (port 80) " +
                "from /172.20.2.84 (port 41234) after 5000ms; v6 from /2001:db8::42",
        )
        assertFalse(out.contains("142.251.13.94"))
        assertFalse(out.contains("172.20.2.84"))
        assertFalse(out.contains("2001:db8::42"))
        assertTrue(out.contains("connectivitycheck.gstatic.com"))
        assertEquals("http://10.0.0.1:8080", LogRedaction.redact("http://10.0.0.1:8080/login?token=s3cret"))
    }

    @Test
    fun `every mac address shape is masked`() {
        assertEquals("wifi mac [mac] joined", LogRedaction.redact("wifi mac 12:34:56:78:9a:bc joined"))
        assertEquals("ap [mac]", LogRedaction.redact("ap AA-BB-CC-DD-EE-FF"))
        assertEquals("UI=JK-000-00_[mac]_2", LogRedaction.redact("UI=JK-000-00_AA-BB-CC-DD-EE-FF_2"))
        assertEquals("cisco [mac]", LogRedaction.redact("cisco 1234.5678.9abc"))
        assertEquals("enc [mac]", LogRedaction.redact("enc 12%3A34%3A56%3A78%3A9A%3ABC"))
        assertEquals("mac=[mac]", LogRedaction.redact("mac=167ada6cf76a"))
    }

    @Test
    fun `timestamps and version numbers are not mistaken for addresses`() {
        assertEquals("at 09:02:32 on 1.1.0", LogRedaction.redact("at 09:02:32 on 1.1.0"))
    }

    @Test
    fun `nothing identifying survives the real portal shapes`() {
        val out = LogRedaction.redact("$portalRedirect then $loyaltyHop")
        for (secret in listOf("12%3A34", "MA=", "SIP=", "172.20.9.99", "AA-BB-CC-DD-EE-FF", "123456789abc", "?", "&")) {
            assertFalse("leaked '$secret' in: $out", out.contains(secret))
        }
        assertTrue(out.contains("secure.11os.com") && out.contains("www.marriott.com"))
    }
}
