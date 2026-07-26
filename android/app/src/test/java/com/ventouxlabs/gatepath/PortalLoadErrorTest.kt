package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.ui.PortalLoadError
import com.ventouxlabs.gatepath.ui.PortalLoadErrorKind
import com.ventouxlabs.gatepath.ui.PortalLoadErrorText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Tests for the portal load-failure model.
 *
 * The property being protected is "no silent blank screen": every failure the
 * WebView can hand us must map to a kind, and every kind must produce copy
 * that actually says something. A regression here doesn't crash — it quietly
 * degrades back to the white screen these tests exist to prevent.
 */
class PortalLoadErrorTest {

    private fun error(
        kind: PortalLoadErrorKind,
        host: String = "portal.airport.net",
    ) = PortalLoadError(kind = kind, host = host, technicalDetail = "code=-1")

    // ── Error-code classification ───────────────────────────────────────────

    @Test
    fun `known WebView error codes map to specific kinds`() {
        // Values are android.webkit.WebViewClient.ERROR_* constants.
        assertEquals(
            PortalLoadErrorKind.HOST_LOOKUP_FAILED,
            PortalLoadErrorKind.fromWebViewErrorCode(-2),
        )
        assertEquals(
            PortalLoadErrorKind.REDIRECT_LOOP,
            PortalLoadErrorKind.fromWebViewErrorCode(-9),
        )
        assertEquals(
            PortalLoadErrorKind.TLS_HANDSHAKE_FAILED,
            PortalLoadErrorKind.fromWebViewErrorCode(-11),
        )
    }

    @Test
    fun `connect, IO and timeout all collapse to UNREACHABLE`() {
        for (code in listOf(-6, -7, -8)) {
            assertEquals(
                "code $code should be UNREACHABLE",
                PortalLoadErrorKind.UNREACHABLE,
                PortalLoadErrorKind.fromWebViewErrorCode(code),
            )
        }
    }

    @Test
    fun `unmapped and unknown codes fall back to UNKNOWN, never crash`() {
        // -1 is ERROR_UNKNOWN; the rest are codes we deliberately don't
        // special-case, plus values outside the documented range entirely.
        for (code in listOf(-1, -3, -5, -12, -16, 0, 42, Int.MIN_VALUE, Int.MAX_VALUE)) {
            assertEquals(
                "code $code should fall back to UNKNOWN",
                PortalLoadErrorKind.UNKNOWN,
                PortalLoadErrorKind.fromWebViewErrorCode(code),
            )
        }
    }

    // ── Copy ────────────────────────────────────────────────────────────────

    @Test
    fun `every kind has a non-blank title and body`() {
        // The whole point is that the user is told something. An empty string
        // here is the white screen with extra steps.
        for (kind in PortalLoadErrorKind.entries) {
            assertTrue("${kind.name} has a blank title", PortalLoadErrorText.title(kind).isNotBlank())
            assertTrue("${kind.name} has a blank body", PortalLoadErrorText.body(error(kind)).isNotBlank())
        }
    }

    @Test
    fun `body names the host when known`() {
        val body = PortalLoadErrorText.body(error(PortalLoadErrorKind.UNREACHABLE, host = "wifi.hotel.example"))
        assertTrue("body should mention the host, was: $body", body.contains("wifi.hotel.example"))
    }

    @Test
    fun `blank host degrades to a readable phrase, not an empty gap`() {
        for (kind in PortalLoadErrorKind.entries) {
            val body = PortalLoadErrorText.body(error(kind, host = ""))
            assertTrue("${kind.name} body should not double-space", !body.contains("  "))
            assertTrue(
                "${kind.name} body should describe the page generically",
                body.contains("this network's sign-in page"),
            )
        }
    }

    @Test
    fun `body never leaks a full URL`() {
        // Portal URLs carry MAC addresses and session tokens; the model takes
        // a host precisely so they can't reach the screen.
        val body = PortalLoadErrorText.body(
            error(PortalLoadErrorKind.UNKNOWN, host = "gw.example.net"),
        )
        assertFalse(body.contains("http://"))
        assertFalse(body.contains("https://"))
        assertFalse(body.contains("?"))
    }

    // ── Retry policy ────────────────────────────────────────────────────────

    @Test
    fun `cert rejection is not retryable`() {
        // Retrying re-rejects, and nudging the user to retry past a possible
        // tampering signal is the wrong affordance.
        assertFalse(PortalLoadErrorText.isRetryable(PortalLoadErrorKind.CERT_REJECTED))
    }

    @Test
    fun `transient failures are retryable`() {
        for (kind in PortalLoadErrorKind.entries - PortalLoadErrorKind.CERT_REJECTED) {
            assertTrue("${kind.name} should be retryable", PortalLoadErrorText.isRetryable(kind))
        }
    }

    @Test
    fun `cert rejection copy warns rather than reassures`() {
        val body = PortalLoadErrorText.body(error(PortalLoadErrorKind.CERT_REJECTED))
        assertTrue("should warn about signing in, was: $body", body.contains("Avoid signing in"))
    }
}
