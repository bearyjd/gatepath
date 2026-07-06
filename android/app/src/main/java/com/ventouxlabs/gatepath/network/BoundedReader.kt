package com.ventouxlabs.gatepath.network

import java.io.ByteArrayOutputStream
import java.io.InputStream

/**
 * Pure, JVM-testable bounded reader used to cap how much of an
 * untrusted-length HTTP body (the Tailscale localapi /v0/status response) is
 * pulled into memory before parsing.
 */
object BoundedReader {

    /**
     * Reads from [input] up to [maxBytes] bytes and decodes them as UTF-8.
     *
     * Returns the decoded content when the source has at most [maxBytes] bytes;
     * returns `null` when it has more (the caller should treat an over-limit
     * body as undeterminable and fail safe). Buffered memory is bounded to
     * roughly [maxBytes] plus one read chunk, regardless of the source's
     * claimed length. Bounding by bytes (not decoded chars) keeps the limit at
     * parity with the desktop detector's byte cap.
     */
    fun readBounded(input: InputStream, maxBytes: Int): String? {
        require(maxBytes >= 0) { "maxBytes must be non-negative, was $maxBytes" }
        val chunk = ByteArray(READ_CHUNK_BYTES)
        val buffer = ByteArrayOutputStream()
        while (buffer.size() <= maxBytes) {
            val n = input.read(chunk)
            if (n < 0) break
            buffer.write(chunk, 0, n)
        }
        return if (buffer.size() > maxBytes) null else buffer.toString(Charsets.UTF_8.name())
    }

    private const val READ_CHUNK_BYTES = 8192
}
