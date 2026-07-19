package com.ventouxlabs.gatepath.network

import android.net.Network
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

// Tight timeouts so multi-request diagnostic probes fit the engine's D3
// per-probe budget; PortalProbe keeps its own longer timeouts for the
// monitoring path.
private const val FETCH_CONNECT_TIMEOUT_MS = 2_000
private const val FETCH_READ_TIMEOUT_MS = 2_000

/** Cap mirrors BoundedReader usage elsewhere: DoH answers and portal pages are tiny. */
private const val MAX_BODY_BYTES = 64 * 1024

/**
 * Outcome of a single no-follow GET. Pure data so diagnostic probes can be
 * driven by fakes; [error] is non-null iff the request failed before an HTTP
 * status was obtained.
 */
data class HttpFetchResult(
    val statusCode: Int?,
    val locationHeader: String?,
    val dateHeaderEpochMillis: Long?,
    val body: String?,
    val error: String?,
)

/**
 * Single-request HTTP GET for the diagnostic battery: redirects are reported,
 * never followed; the `Date` header is surfaced for clock-skew detection; the
 * body is capped via [BoundedReader.readBounded]. Like [PortalProbe],
 * [Network] is nullable so the class is JVM-testable (null = default socket /
 * route).
 *
 * Note: [BoundedReader.readBounded] returns `null` when the body exceeds the
 * cap (its fail-safe contract), not a truncated prefix — so an over-cap body
 * surfaces here as `body = null` rather than a partial read.
 */
class HttpFetcher {

    suspend fun fetch(
        network: Network?,
        url: String,
        accept: String? = null,
    ): HttpFetchResult = withContext(Dispatchers.IO) {
        runCatching {
            val u = URL(url)
            val conn = (if (network != null) network.openConnection(u) else u.openConnection()) as HttpURLConnection
            conn.apply {
                instanceFollowRedirects = false
                connectTimeout = FETCH_CONNECT_TIMEOUT_MS
                readTimeout = FETCH_READ_TIMEOUT_MS
                requestMethod = "GET"
                if (accept != null) setRequestProperty("Accept", accept)
            }
            try {
                conn.connect()
                val code = conn.responseCode
                val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                val body = stream?.let { s ->
                    s.use { BoundedReader.readBounded(it, MAX_BODY_BYTES) }
                }
                HttpFetchResult(
                    statusCode = code,
                    locationHeader = conn.getHeaderField("Location"),
                    dateHeaderEpochMillis = parseHttpDate(conn.getHeaderField("Date")),
                    body = body,
                    error = null,
                )
            } finally {
                conn.disconnect()
            }
        }.getOrElse { ex ->
            HttpFetchResult(null, null, null, null, ex.message ?: ex.javaClass.simpleName)
        }
    }

    private fun parseHttpDate(value: String?): Long? {
        if (value == null) return null
        return runCatching {
            ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME)
                .toInstant()
                .toEpochMilli()
        }.getOrNull()
    }
}
