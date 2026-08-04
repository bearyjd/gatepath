package com.ventouxlabs.gatepath.network

import java.security.MessageDigest

/**
 * Privacy-preserving evidence from an intercepted connectivity-check response.
 * It deliberately excludes bodies, cookies, and portal URLs: those frequently
 * carry device identifiers or one-time credentials.
 *
 * The one header value it keeps is [contentType], bounded to 128 characters —
 * a media type describes the gateway's own response rather than the device, so
 * it reveals nothing the gateway did not already send us.
 *
 * This is exported by `DiagnosticsBundle` with **no redaction pass in either
 * mode**, so every field here must stay non-identifying by construction.
 * `DiagnosticsBundleTest` guards the field set against drift.
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
        /** Hash a bounded, decoded body; never retain the body itself. */
        fun sha256(body: String): String = MessageDigest.getInstance("SHA-256")
            .digest(body.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }
}
