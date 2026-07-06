package com.ventouxlabs.gatepath.network

import java.io.Reader

/**
 * Pure, JVM-testable bounded reader used to cap how much of an
 * untrusted-length HTTP body (the Tailscale localapi /v0/status response) is
 * pulled into memory before parsing.
 */
object BoundedReader {

    /**
     * Reads from [reader] up to [maxChars] characters.
     *
     * Returns the full content when the source has at most [maxChars]
     * characters; returns `null` when it has more (the caller should treat an
     * over-limit body as undeterminable and fail safe). Memory use is bounded
     * to roughly [maxChars] plus one read chunk, regardless of how large the
     * source claims to be.
     */
    fun readBounded(reader: Reader, maxChars: Int): String? {
        require(maxChars >= 0) { "maxChars must be non-negative, was $maxChars" }
        val chunk = CharArray(READ_CHUNK_CHARS)
        val sb = StringBuilder()
        while (sb.length <= maxChars) {
            val n = reader.read(chunk)
            if (n < 0) break
            sb.append(chunk, 0, n)
        }
        return if (sb.length > maxChars) null else sb.toString()
    }

    private const val READ_CHUNK_CHARS = 8192
}
