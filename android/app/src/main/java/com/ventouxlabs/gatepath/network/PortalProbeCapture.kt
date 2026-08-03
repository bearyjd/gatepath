package com.ventouxlabs.gatepath.network

import java.security.MessageDigest

/**
 * Privacy-preserving evidence from an intercepted connectivity-check response.
 * It deliberately excludes bodies, cookies, header values, and portal URLs:
 * those frequently carry device identifiers or one-time credentials.
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
