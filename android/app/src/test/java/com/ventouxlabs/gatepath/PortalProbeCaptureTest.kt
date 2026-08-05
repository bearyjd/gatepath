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
 *
 * `normalizeContentType` is `internal`; these tests reach it only because
 * run-jvm-tests.sh passes -Xfriend-paths.
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
        // Note a valid RFC 6838 token pair is not enough: `text/html-abc123` is
        // well-formed, which is why this is an allowlist and not a shape check.
        assertEquals(
            PortalProbeCapture.OTHER_MEDIA_TYPE,
            PortalProbeCapture.normalizeContentType("text/html-abc123device"),
        )
        assertEquals(
            PortalProbeCapture.OTHER_MEDIA_TYPE,
            PortalProbeCapture.normalizeContentType("application/x-tracked-abc123device"),
        )
        assertEquals(
            PortalProbeCapture.OTHER_MEDIA_TYPE,
            PortalProbeCapture.normalizeContentType(""),
        )
    }

    /**
     * The invariant behind the private constructor: normalisation is not
     * something a caller has to remember, because [PortalProbeCapture.of] is
     * the only way in. A capture holding a raw header should be impossible to
     * build, not merely discouraged by a docstring.
     */
    @Test
    fun `the factory normalises, so an un-normalised capture cannot be built`() {
        val capture = PortalProbeCapture.of(
            httpStatus = 200,
            rawContentType = "text/html; session=abc123device",
            redirectSignal = PortalProbeCapture.RedirectSignal.SCRIPTED_LOCATION,
        )

        assertEquals("text/html", capture.contentType)
        assertEquals(200, capture.httpStatus)
        assertEquals(PortalProbeCapture.RedirectSignal.SCRIPTED_LOCATION, capture.redirectSignal)
    }

    @Test
    fun `the factory passes a null header through as null`() {
        val capture = PortalProbeCapture.of(
            httpStatus = 302,
            rawContentType = null,
            redirectSignal = PortalProbeCapture.RedirectSignal.LOCATION_HEADER,
        )

        assertNull(capture.contentType)
    }
}
