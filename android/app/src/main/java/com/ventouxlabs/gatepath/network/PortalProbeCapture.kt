package com.ventouxlabs.gatepath.network

import java.security.MessageDigest

/**
 * Privacy-preserving evidence from an intercepted connectivity-check response.
 * It deliberately excludes bodies, cookies, and portal URLs: those frequently
 * carry device identifiers or one-time credentials.
 *
 * A response header is arbitrary gateway-controlled text, not reliably a media
 * type, so [contentType] is never stored raw: [normalizeContentType] collapses
 * it to a known media type or [OTHER_MEDIA_TYPE]. Without that, a hostile
 * gateway could smuggle a per-device value (`text/html; session=<id>`) into a
 * bundle the user shares with someone else.
 *
 * [bodySha256] is only non-identifying while the portal body is impersonal:
 * for a personalised page the digest is a stable fingerprint that anyone
 * holding candidate responses can confirm by matching. `DiagnosticsBundle`
 * therefore omits it from a redacted bundle.
 *
 * `DiagnosticsBundleTest` guards this field set against drift — a new field
 * must be shown non-identifying, or given redaction, before it is exported.
 */
data class PortalProbeCapture(
    val httpStatus: Int,
    val contentType: String?,
    val redirectSignal: RedirectSignal,
    val bodyCharacters: Int?,
    val bodySha256: String?,
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
         * Reduce a raw `Content-Type` to a known media type, dropping every
         * parameter (`; charset=...` and anything a gateway invents). Unknown
         * types collapse to [OTHER_MEDIA_TYPE] rather than being echoed.
         */
        fun normalizeContentType(raw: String?): String? {
            if (raw == null) return null
            val mediaType = raw.substringBefore(';').trim().lowercase()
            return if (mediaType in ALLOWED_MEDIA_TYPES) mediaType else OTHER_MEDIA_TYPE
        }

        /** Hash a bounded, decoded body; never retain the body itself. */
        fun sha256(body: String): String = MessageDigest.getInstance("SHA-256")
            .digest(body.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }
}
