package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.BoundedReader
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.ByteArrayInputStream

class BoundedReaderTest {

    private fun source(s: String) = ByteArrayInputStream(s.toByteArray(Charsets.UTF_8))

    @Test
    fun `reads a body under the limit`() {
        assertEquals("hello", BoundedReader.readBounded(source("hello"), 100))
    }

    @Test
    fun `reads a body exactly at the limit`() {
        val body = "x".repeat(100)
        assertEquals(body, BoundedReader.readBounded(source(body), 100))
    }

    @Test
    fun `empty source yields empty string`() {
        assertEquals("", BoundedReader.readBounded(source(""), 100))
    }

    @Test
    fun `returns null when the source exceeds the limit`() {
        val body = "x".repeat(101)
        assertNull(BoundedReader.readBounded(source(body), 100))
    }

    @Test
    fun `rejects at the right boundary across a chunk edge`() {
        // Limit deliberately not a multiple of the 8192-byte read chunk, so the
        // boundary falls mid-chunk and exercises multi-read accumulation.
        val limit = 8192 + 5
        assertEquals("z".repeat(limit), BoundedReader.readBounded(source("z".repeat(limit)), limit))
        assertNull(BoundedReader.readBounded(source("z".repeat(limit + 1)), limit))
    }

    @Test
    fun `bounds a source far larger than the limit`() {
        // A ~1 MiB source must be rejected against a small limit without
        // reading it all into memory.
        val body = "y".repeat(1_048_576)
        assertNull(BoundedReader.readBounded(source(body), 4096))
    }
}
