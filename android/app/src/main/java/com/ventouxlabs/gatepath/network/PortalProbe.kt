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
 *   200 OK               → ALSO treated as portal. gstatic never returns 200 for
 *                          this endpoint, so a 200 means something rewrote the
 *                          response — i.e. a captive gateway serving its login
 *                          page in place (Cisco/Meraki/Cloudflare-style
 *                          intercepts do this instead of redirecting). The
 *                          portal location comes from a `Refresh` header or
 *                          meta-refresh when present (see [PortalRedirectHint]),
 *                          otherwise the probe URL itself, which the gateway is
 *                          already intercepting.
 *   timeout, other codes → unexpected, returned as ProbeResult.Error
 *
 * This matches the desktop probe (desktop/gatepath/portal_probe.py), which has
 * always classified 200 as a portal. Android previously returned Error here,
 * which meant a 200-style portal was never detected at all: both probe paths in
 * CaptivePortalMonitor failed and the flow emitted CaptivePortalSuspected
 * instead of opening the sign-in WebView.
 */
const val CONNECTIVITY_CHECK_URL = "http://connectivitycheck.gstatic.com/generate_204"

private const val CONNECT_TIMEOUT_MS = 5_000
private const val READ_TIMEOUT_MS = 5_000

/**
 * Cap on how much of a 200 response body is pulled in while looking for a
 * meta-refresh. Matches [PortalRedirectHint.MAX_HTML_SCAN_CHARS]; a body larger
 * than this yields no hint and the probe falls back to the probe URL.
 */
private const val MAX_HINT_BODY_BYTES = 64 * 1024

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
                    code == HttpURLConnection.HTTP_OK -> {
                        // A 200 on the connectivity-check URL is an intercept.
                        // Look for an onward hop in the Refresh header or a
                        // meta-refresh; the body is untrusted gateway output,
                        // so the read is byte-bounded and any failure degrades
                        // to "no hint" rather than propagating.
                        val hint = runCatching {
                            PortalRedirectHint.resolve(
                                refreshHeader = conn.getHeaderField("Refresh"),
                                html = BoundedReader.readBounded(
                                    conn.inputStream,
                                    MAX_HINT_BODY_BYTES,
                                ),
                                baseUrl = testUrl,
                            )
                        }.getOrNull()
                        // Falling back to testUrl is safe: the gateway is
                        // already intercepting it, so loading it in the WebView
                        // yields the same login page the probe just received.
                        ProbeResult.Portal(locationUrl = hint ?: testUrl)
                    }
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
