package com.ventouxlabs.gatepath.network

import android.net.Network
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Standard Android connectivity-check URL (Google's generate_204 endpoint).
 *
 * MUST be HTTP, not HTTPS — captive portals work by intercepting cleartext
 * HTTP and redirecting to a sign-in page. An HTTPS check would either succeed
 * (TLS termination by the portal, broken cert validation) or fail with a
 * cert error, neither of which distinguishes "captive portal active" from
 * "internet down."
 *
 * Expected responses for this specific URL:
 *   204 No Content       → connectivity validated, no portal
 *   301/302/307/308      → captive portal redirect; Location header is the portal URL
 *   anything else (200,
 *     timeout, etc.)     → unexpected, returned as ProbeResult.Error
 *
 * Note: 200 OK is treated as Error here because gstatic always returns 204
 * or a redirect. Other connectivity-check URLs (e.g., the one NetworkManager
 * picks on Linux) may legitimately return 200 with a portal page; the desktop
 * probe handles that case differently.
 */
const val CONNECTIVITY_CHECK_URL = "http://connectivitycheck.gstatic.com/generate_204"

private const val CONNECT_TIMEOUT_MS = 5_000
private const val READ_TIMEOUT_MS = 5_000

/**
 * Result of a single captive-portal probe.
 * Sealed interface — exhaustive when() required.
 */
sealed interface ProbeResult {
    /** HTTP 204: connectivity is fine, no captive portal. */
    data object Validated : ProbeResult

    /** HTTP 301/302/307/308: captive portal detected at [locationUrl]. */
    data class Portal(val locationUrl: String) : ProbeResult

    /** Network error or unexpected response. */
    data class Error(val message: String) : ProbeResult
}

/**
 * Pure networking class: probes for captive portals using only [Network.openConnection].
 * [network] is nullable so the class is testable on plain JVM (network=null falls back
 * to the default JVM socket).
 */
class PortalProbe {

    /**
     * Probe [testUrl] on the given [network] (or the default socket if null).
     * Must be called from a coroutine; executes on [Dispatchers.IO].
     */
    suspend fun probe(
        network: Network? = null,
        testUrl: String = CONNECTIVITY_CHECK_URL,
    ): ProbeResult = withContext(Dispatchers.IO) {
        runCatching {
            val url = URL(testUrl)
            val conn = (if (network != null) {
                network.openConnection(url)
            } else {
                url.openConnection()
            }) as HttpURLConnection

            conn.apply {
                instanceFollowRedirects = false
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                requestMethod = "GET"
            }

            try {
                conn.connect()
                val code = conn.responseCode
                when {
                    code == HttpURLConnection.HTTP_NO_CONTENT -> ProbeResult.Validated
                    code in 300..399 -> {
                        val location = conn.getHeaderField("Location")
                        if (location != null) {
                            ProbeResult.Portal(locationUrl = location)
                        } else {
                            ProbeResult.Error("Redirect with no Location header (code=$code)")
                        }
                    }
                    else -> ProbeResult.Error("Unexpected HTTP status: $code")
                }
            } finally {
                conn.disconnect()
            }
        }.getOrElse { ex ->
            ProbeResult.Error(ex.message ?: ex.javaClass.simpleName)
        }
    }
}
