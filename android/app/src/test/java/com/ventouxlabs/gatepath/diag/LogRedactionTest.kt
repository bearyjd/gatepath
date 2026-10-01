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

    @Test
    fun `a url keeps only its scheme and host`() {
        assertEquals("https://secure.11os.com", LogRedaction.redact(portalRedirect))
        assertEquals("http://10.0.0.1:8080", LogRedaction.redact("http://10.0.0.1:8080/login?token=s3cret"))
    }

    @Test
    fun `urls inside free text are reduced and the rest of the text is kept`() {
        assertEquals(
            "Page started: https://www.marriott.com (after 2 hops)",
            LogRedaction.redact("Page started: $loyaltyHop (after 2 hops)"),
        )
    }

    @Test
    fun `credentials in a url never survive`() {
        val out = LogRedaction.redact("loading https://guest:hunter2@portal.example.net/login")
        assertEquals("loading https://portal.example.net", out)
    }

    @Test
    fun `mac addresses outside urls are masked`() {
        assertEquals("wifi mac [mac] joined", LogRedaction.redact("wifi mac 12:34:56:78:9a:bc joined"))
        assertEquals("ap [mac]", LogRedaction.redact("ap AA-BB-CC-DD-EE-FF"))
    }

    @Test
    fun `nothing identifying survives the real portal shapes`() {
        val out = LogRedaction.redact("$portalRedirect then $loyaltyHop")
        for (secret in listOf("12%3A34", "MA=", "SIP=", "172.20.9.99", "AA-BB-CC-DD-EE-FF", "123456789abc", "?", "&")) {
            assertFalse("leaked '$secret' in: $out", out.contains(secret))
        }
        assertTrue(out.contains("secure.11os.com") && out.contains("www.marriott.com"))
    }

    @Test
    fun `host helper returns the bare host or a placeholder`() {
        assertEquals("zqqwqihz.gatewayauth.com", LogRedaction.host("https://zqqwqihz.gatewayauth.com/login?x=1"))
        assertEquals("(none)", LogRedaction.host(null))
        assertEquals("(no host)", LogRedaction.host("javascript:alert(1)"))
    }
}
