package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.PortalRedirectHint
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Unit tests for [PortalRedirectHint] — the parser that extracts a portal
 * location from a 200-response's `Refresh` header or HTML meta-refresh.
 *
 * Every input here is attacker-controlled in production: the bytes come from
 * whatever gateway is intercepting the connectivity check, before the user has
 * authenticated to anything. The rejection cases below are the security
 * surface, not edge-case polish.
 */
class PortalRedirectHintTest {

    private val base = "http://connectivitycheck.gstatic.com/generate_204"

    // ── Refresh header ──────────────────────────────────────────────────────

    @Test
    fun `refresh header with url parameter is extracted`() {
        assertEquals(
            "http://portal.example.com/login",
            PortalRedirectHint.resolve(
                refreshHeader = "0; url=http://portal.example.com/login",
                html = null,
                baseUrl = base,
            ),
        )
    }

    @Test
    fun `refresh header is case insensitive and tolerates quotes`() {
        assertEquals(
            "https://portal.example.com/login",
            PortalRedirectHint.resolve(
                refreshHeader = "5;URL='https://portal.example.com/login'",
                html = null,
                baseUrl = base,
            ),
        )
    }

    @Test
    fun `refresh header with only a delay yields no hint`() {
        assertNull(PortalRedirectHint.resolve("0", null, base))
    }

    @Test
    fun `relative refresh target resolves against the probe url`() {
        assertEquals(
            "http://connectivitycheck.gstatic.com/login.html",
            PortalRedirectHint.resolve("0; url=/login.html", null, base),
        )
    }

    // ── HTML meta refresh ───────────────────────────────────────────────────

    @Test
    fun `meta refresh is extracted from html`() {
        val html = """
            <html><head>
            <meta http-equiv="refresh" content="0; url=http://portal.example.com/welcome">
            </head><body>redirecting</body></html>
        """.trimIndent()
        assertEquals(
            "http://portal.example.com/welcome",
            PortalRedirectHint.resolve(null, html, base),
        )
    }

    @Test
    fun `meta refresh tolerates single quotes and attribute reordering`() {
        val html = "<meta content='0;url=http://portal.example.com/x' http-equiv='REFRESH'>"
        assertEquals(
            "http://portal.example.com/x",
            PortalRedirectHint.resolve(null, html, base),
        )
    }

    @Test
    fun `html without meta refresh yields no hint`() {
        val html = "<html><body><form action='/login'></form></body></html>"
        assertNull(PortalRedirectHint.resolve(null, html, base))
    }

    // ── Precedence ──────────────────────────────────────────────────────────

    @Test
    fun `refresh header wins over meta refresh`() {
        val html = "<meta http-equiv='refresh' content='0;url=http://from-html.example.com/'>"
        assertEquals(
            "http://from-header.example.com/",
            PortalRedirectHint.resolve(
                refreshHeader = "0; url=http://from-header.example.com/",
                html = html,
                baseUrl = base,
            ),
        )
    }

    // ── Security: scheme rejection ──────────────────────────────────────────

    @Test
    fun `javascript scheme is rejected`() {
        assertNull(PortalRedirectHint.resolve("0; url=javascript:alert(1)", null, base))
        assertNull(
            PortalRedirectHint.resolve(
                null,
                "<meta http-equiv='refresh' content='0;url=javascript:alert(1)'>",
                base,
            ),
        )
    }

    @Test
    fun `data scheme is rejected`() {
        assertNull(
            PortalRedirectHint.resolve("0; url=data:text/html;base64,PHNjcmlwdD4=", null, base),
        )
    }

    @Test
    fun `file scheme is rejected`() {
        assertNull(PortalRedirectHint.resolve("0; url=file:///data/data/com.ventouxlabs.gatepath/", null, base))
    }

    @Test
    fun `intent scheme is rejected`() {
        assertNull(
            PortalRedirectHint.resolve("0; url=intent://evil#Intent;scheme=http;end", null, base),
        )
    }

    @Test
    fun `malformed target yields no hint rather than throwing`() {
        assertNull(PortalRedirectHint.resolve("0; url=http://[not-a-url", null, base))
        assertNull(PortalRedirectHint.resolve("0; url=", null, base))
    }

    // ── Null / empty inputs ─────────────────────────────────────────────────

    @Test
    fun `null inputs yield no hint`() {
        assertNull(PortalRedirectHint.resolve(null, null, base))
    }

    @Test
    fun `oversized html is not scanned past the cap`() {
        // A hint buried past the scan cap must not be found — this bounds the
        // regex work done on a hostile body.
        val padding = " ".repeat(PortalRedirectHint.MAX_HTML_SCAN_CHARS + 100)
        val html = "$padding<meta http-equiv='refresh' content='0;url=http://late.example.com/'>"
        assertNull(PortalRedirectHint.resolve(null, html, base))
    }
}
