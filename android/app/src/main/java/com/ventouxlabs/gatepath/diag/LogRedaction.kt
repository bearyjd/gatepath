package com.ventouxlabs.gatepath.diag

import java.net.URI

/**
 * Strips device identifiers from text bound for the exported sign-in log.
 *
 * Captive-portal URLs carry the device's MAC address, the gateway's internal
 * IP and session tokens in their paths and query strings (a real hotel portal
 * put `MA=<mac>` and `SIP=<ip>` in every redirect). The log leaves the device
 * through the share sheet, so every URL is reduced to scheme + host (+ port),
 * user-info is dropped, and bare MAC addresses are masked. Pure Kotlin so the
 * redaction is JVM-tested.
 */
object LogRedaction {

    private val URL = Regex("""\b(https?)://([^\s/?#]+)[^\s]*""", RegexOption.IGNORE_CASE)
    private val MAC = Regex("""\b[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}\b""")

    /** [text] with every URL reduced to scheme + host and every MAC address masked. */
    fun redact(text: String): String {
        val urlsReduced = URL.replace(text) { m ->
            val scheme = m.groupValues[1].lowercase()
            val authority = m.groupValues[2].substringAfterLast('@')
            "$scheme://$authority"
        }
        return MAC.replace(urlsReduced, "[mac]")
    }

    /** The bare host of [url], `(none)` for null, `(no host)` when it has none. */
    fun host(url: String?): String {
        if (url == null) return "(none)"
        return runCatching { URI(url).host }.getOrNull()?.takeIf { it.isNotEmpty() } ?: "(no host)"
    }
}
