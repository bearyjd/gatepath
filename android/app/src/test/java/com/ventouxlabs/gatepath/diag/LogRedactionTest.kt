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
    fun `an ip glued to a letter, digit or underscore is still masked`() {
        assertEquals("SIP_[ip] ip[ip] gw[ip]x", LogRedaction.redact("SIP_172.20.9.99 ip172.20.9.98 gw172.20.9.97x"))
    }

    @Test(timeout = 2_000)
    fun `a long dotted run of digits is masked whole without overflowing`() {
        // A repeated group in the IPv4 pattern overflowed Java's stack at a few thousand octets.
        assertEquals("x [ip]", LogRedaction.redact("x " + "1.".repeat(64_000) + "1"))
    }

    @Test
    fun `timestamps and version numbers are not mistaken for addresses`() {
        assertEquals("at 09:02:32 on 1.1.0", LogRedaction.redact("at 09:02:32 on 1.1.0"))
    }

    // ── percent-encoding: decoded before anything is matched ────────────────

    @Test
    fun `a fully percent-encoded url is reduced to its origin`() {
        assertEquals(
            "redirect target was https://h",
            LogRedaction.redact("redirect target was https%3A%2F%2Fh%2F%3FMA%3D12%3A34%3A56%3A78%3A9A%3ABC"),
        )
    }

    @Test
    fun `an encoded delimiter does not shield what follows it`() {
        // Every %XY ends in a hex digit, which reads as a word character to each matcher.
        assertEquals("x SIP=[ip]", LogRedaction.redact("x SIP%3D172.20.9.99"))
        assertEquals("x &MA=[mac]", LogRedaction.redact("x %26MA%3D123456789abc"))
        assertEquals("x =[mac]", LogRedaction.redact("x %3DAA-BB-CC-DD-EE-FF"))
        assertEquals("portal at /login.php", LogRedaction.redact("portal at /login.php%3Ftok%3Ds3cr3t"))
    }

    @Test
    fun `nested percent-encoding is decoded all the way`() {
        assertEquals("nested MA=[mac]", LogRedaction.redact("nested MA%253D12%253A34%253A56%253A78%253A9A%253ABC"))
    }

    @Test
    fun `decoding never splits a url and lets its tail escape`() {
        assertEquals("https://h.example", LogRedaction.redact("https://h.example/a%20session-9f3k2"))
        assertEquals("https://h.example", LogRedaction.redact("https://h.example/a%28x%29s3cret%22%3Cb%3E%27"))
        assertEquals("/x", LogRedaction.redact("/x?tok=a%0As3cret%09more"))
    }

    @Test
    fun `stray percent signs and non-ascii escapes are left alone`() {
        assertEquals("battery at 100% for caf%C3%A9", LogRedaction.redact("battery at 100% for caf%C3%A9"))
    }

    @Test(timeout = 2_000)
    fun `decoding stays linear on deeply nested escapes`() {
        // A gateway controls the probe's error text, recorded on the main thread.
        assertEquals("deep %41", LogRedaction.redact("deep %" + "25".repeat(50_000) + "41"))
    }

    @Test
    fun `an escaped letter or digit after an identifier does not hide it`() {
        assertEquals("SIP=[ip]%31%32", LogRedaction.redact("SIP=172.20.9.99%31%32"))
        assertEquals("MA=[mac]%41", LogRedaction.redact("MA=aa:bb:cc:dd:ee:ff%41"))
        assertEquals("from [ip]%2E", LogRedaction.redact("from 2001:db8::1%2E"))
        assertEquals("[mac]%2E", LogRedaction.redact("aabb.ccdd.eeff%2E"))
    }

    @Test
    fun `a url nested in a query goes with the query`() {
        assertEquals(
            "Unexpected status line: /login",
            LogRedaction.redact("Unexpected status line: /login?next=http%3A%2F%2F172.20.9.99%2Fx%3Fmac%3D123456789abc"),
        )
        assertEquals("x /login", LogRedaction.redact("x /login?r=ws%3A%2F%2Faa-bb-cc-dd-ee-ff%2F"))
        assertEquals("intent:", LogRedaction.redact("intent:#Intent;S.url=http%3A%2F%2Fh%2F%3Fmac%3D123456789abc;end"))
    }

    @Test
    fun `a mac or a token is never printed as a url's scheme or host`() {
        assertEquals("id [mac]://x", LogRedaction.redact("id aa-bb-cc-dd-ee-ff%3A%2F%2Fx"))
        val out = LogRedaction.redact("x%3A%2F%2FMA=12:34:56:78:9A:BC then https://h.example;jsessionid=ABC123/login")
        for (secret in listOf("12:34", "MA=", "jsessionid", "ABC123")) assertFalse("leaked '$secret' in: $out", out.contains(secret))
    }

    @Test
    fun `line breaks, controls and unicode spaces become plain spaces`() {
        assertEquals("a b c d e f g", LogRedaction.redact("a\nb\rc d\u0085e f\tg"))
    }

    @Test
    fun `other controls become a replacement character and end nothing early`() {
        // A space would end the query or URL run, and the tail would print as plain text.
        assertEquals("a�b�c�d", LogRedaction.redact("a\u0001b\u001Cc\u007Fd"))
        assertEquals("x /login", LogRedaction.redact("x /login?a=1\u0001tok=SECRET123"))
        assertEquals("x http://h", LogRedaction.redact("x http://h/p\u0001/session/8f3a9c2e1b"))
    }

    @Test
    fun `a url whose scheme is a mac keeps only its host`() {
        assertEquals("x [mac]://portal", LogRedaction.redact("x aa-bb-cc-dd-ee-ff://portal/session/8f3a9c2e1b"))
        assertEquals("x [mac]://h", LogRedaction.redact("x aabb.ccdd.eeff://h/tok/ABC123"))
        assertEquals("x [mac]://h", LogRedaction.redact("x 12:34:56:78:9a:bc://h/session/8f3a9c2e1b"))
        assertEquals("x[mac]://h", LogRedaction.redact("xaa-bb-cc-dd-ee-ff://h/session/8f3a9c2e1b"))
        assertEquals("id [mac]://x", LogRedaction.redact("id aa-bb-cc-dd-ee-ff%3A%2F%2Fx%2Fsession%2F8f3a9c2e1b"))
    }

    @Test
    fun `a url glued to a digit, underscore or non-ascii letter is still reduced`() {
        assertEquals("x http://h", LogRedaction.redact("x 1http://h/session/8f3a9c2e1b"))
        assertEquals("x a_http://h", LogRedaction.redact("x a_http://h/session/8f3a9c2e1b"))
        assertEquals("éhttp://h", LogRedaction.redact("éhttp://h/session/8f3a9c2e1b"))
        assertEquals("接続http://h", LogRedaction.redact("接続http://h/session/8f3a9c2e1b"))
    }

    @Test(timeout = 2_000)
    fun `url matching stays linear on long runs of scheme characters`() {
        // A gateway controls the probe's error text, recorded on the main thread.
        val dotted = "Unexpected status line: " + "a.".repeat(64_000)
        assertEquals(dotted, LogRedaction.redact(dotted))
        val hyphenated = "-a".repeat(32_000)
        assertEquals(hyphenated, LogRedaction.redact(hyphenated))
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
