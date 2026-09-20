package com.ventouxlabs.gatepath.diag

import java.security.MessageDigest

/**
 * What a certificate error looked like, without anything the gateway wrote.
 * Subject and issuer are gateway-authored strings, so they are not here; the
 * SHA-256 fingerprint identifies the certificate without echoing it.
 * `CertSummaryTest` guards the field set.
 */
@ConsistentCopyVisibility
data class CertSummary private constructor(
    /** `android.net.http.SslError` primary error code (0..5). */
    val primaryError: Int,
    val notBeforeEpochMillis: Long?,
    val notAfterEpochMillis: Long?,
    val selfSigned: Boolean,
    /** Lowercase hex; empty when the DER bytes were unavailable. */
    val sha256Fingerprint: String,
) {
    companion object {
        fun of(
            primaryError: Int,
            notBefore: Long?,
            notAfter: Long?,
            subjectEqualsIssuer: Boolean,
            derEncoded: ByteArray?,
        ): CertSummary = CertSummary(
            primaryError = primaryError,
            notBeforeEpochMillis = notBefore,
            notAfterEpochMillis = notAfter,
            selfSigned = subjectEqualsIssuer,
            sha256Fingerprint = derEncoded?.let { sha256Hex(it) } ?: "",
        )

        private fun sha256Hex(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    }
}
