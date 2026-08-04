package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.PortalProbeCapture
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Pure-JVM tests for the capture's privacy narrowing.
 *
 * [PortalProbeCapture] is exported into the diagnostics bundle the user shares,
 * so the values it accepts from a hostile gateway matter more than the ones it
 * rejects. `Content-Type` is the interesting one: it looks like a media type
 * but is arbitrary gateway-controlled text.
 */
class PortalProbeCaptureTest {

    @Test
    fun `a known media type survives intact`() {
        assertEquals("text/html", PortalProbeCapture.normalizeContentType("text/html"))
        assertEquals(
            "application/json",
            PortalProbeCapture.normalizeContentType("application/json"),
        )
    }

    @Test
    fun `an absent header stays absent rather than becoming other`() {
        assertNull(PortalProbeCapture.normalizeContentType(null))
    }

    @Test
    fun `parameters are dropped, benign or not`() {
        assertEquals(
            "text/html",
            PortalProbeCapture.normalizeContentType("text/html; charset=UTF-8"),
        )
        // The reason this normalisation exists: a gateway can hang a per-device
        // value off the header and it would otherwise land in a shared bundle.
        assertEquals(
            "text/html",
            PortalProbeCapture.normalizeContentType("text/html; session=abc123device"),
        )
    }

    @Test
    fun `case and surrounding whitespace do not defeat the allowlist`() {
        assertEquals("text/html", PortalProbeCapture.normalizeContentType("TEXT/HTML"))
        assertEquals("text/html", PortalProbeCapture.normalizeContentType("  text/html  "))
    }

    @Test
    fun `an unrecognised type collapses instead of being echoed`() {
        // Echoing the raw value would reopen exactly the channel the allowlist
        // closes, so an unknown type must not appear in the output at all.
        assertEquals(
            PortalProbeCapture.OTHER_MEDIA_TYPE,
            PortalProbeCapture.normalizeContentType("application/x-tracked-abc123device"),
        )
        assertEquals(
            PortalProbeCapture.OTHER_MEDIA_TYPE,
            PortalProbeCapture.normalizeContentType("abc123device"),
        )
        assertEquals(
            PortalProbeCapture.OTHER_MEDIA_TYPE,
            PortalProbeCapture.normalizeContentType(""),
        )
    }

    /**
     * Known-answer test. The digest is built by hex-formatting signed bytes,
     * which is a classic place to lose the high bit, so pin it to published
     * SHA-256 vectors rather than to whatever the implementation returns.
     */
    @Test
    fun `sha256 matches published vectors`() {
        assertEquals(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            PortalProbeCapture.sha256("abc"),
        )
        assertEquals(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            PortalProbeCapture.sha256(""),
        )
    }
}
