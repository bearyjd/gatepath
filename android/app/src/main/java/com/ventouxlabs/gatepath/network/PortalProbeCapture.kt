package com.ventouxlabs.gatepath.network

/**
 * Privacy-preserving evidence from an intercepted connectivity-check response.
 *
 * Every field here is exported into a diagnostics bundle the user shares
 * off-device, so the type only admits values that are safe to send. Build one
 * through [of] — the constructor is private precisely so a caller cannot hand
 * in a raw response header.
 *
 * ### What it deliberately does not carry
 * Bodies, body digests, body lengths, cookies, and portal URLs. Anything
 * derived from the response body is gateway-controlled and can be varied per
 * device — padding changes a length, personalised markup changes a digest — so
 * it is an identifier channel into a bundle the user believes is scrubbed. A
 * digest was carried here briefly on the theory that comparing digests across
 * probes was useful; nothing in the app compares them, so it earned nothing and
 * cost a consent problem.
 *
 * [contentType] is the one header value kept, and only after [normalizeContentType]
 * reduces it to a known media type. A response header is arbitrary
 * gateway-controlled text, not reliably a media type: `text/html; session=<id>`
 * is a valid header.
 *
 * `DiagnosticsBundleTest` guards this field set against drift.
 */
@ConsistentCopyVisibility
data class PortalProbeCapture private constructor(
    val httpStatus: Int,
    val contentType: String?,
    val redirectSignal: RedirectSignal,
) {
    enum class RedirectSignal { LOCATION_HEADER, REFRESH_HEADER, META_REFRESH, SCRIPTED_LOCATION, NONE }

    companion object {
        /** Stand-in for any media type outside [ALLOWED_MEDIA_TYPES]. */
        const val OTHER_MEDIA_TYPE = "(other)"

        /**
         * Media types kept verbatim. The diagnostic question is only "what kind
         * of response did the gateway return", so an allowlist answers it in
         * full without ever retaining gateway-authored free text.
         */
        private val ALLOWED_MEDIA_TYPES = setOf(
            "text/html",
            "text/plain",
            "text/xml",
            "text/css",
            "text/javascript",
            "application/xhtml+xml",
            "application/json",
            "application/xml",
            "application/javascript",
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/svg+xml",
        )

        /**
         * The only way to build a capture. [rawContentType] is the response
         * header verbatim; narrowing happens here so an un-normalised capture
         * is unrepresentable rather than merely discouraged. Callers pass what
         * the connection gave them and cannot get it wrong.
         */
        fun of(
            httpStatus: Int,
            rawContentType: String?,
            redirectSignal: RedirectSignal,
        ): PortalProbeCapture = PortalProbeCapture(
            httpStatus = httpStatus,
            contentType = normalizeContentType(rawContentType),
            redirectSignal = redirectSignal,
        )

        /**
         * Reduce a raw `Content-Type` to a known media type, dropping every
         * parameter (`; charset=...` and anything a gateway invents). Unknown
         * types collapse to [OTHER_MEDIA_TYPE] rather than being echoed.
         */
        internal fun normalizeContentType(raw: String?): String? {
            if (raw == null) return null
            val mediaType = raw.substringBefore(';').trim().lowercase()
            return if (mediaType in ALLOWED_MEDIA_TYPES) mediaType else OTHER_MEDIA_TYPE
        }
    }
}
