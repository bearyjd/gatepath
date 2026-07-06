package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.BoundedReader
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.StringReader

class BoundedReaderTest {

    @Test
    fun `reads a body under the limit`() {
        assertEquals("hello", BoundedReader.readBounded(StringReader("hello"), 100))
    }

    @Test
    fun `reads a body exactly at the limit`() {
        val body = "x".repeat(100)
        assertEquals(body, BoundedReader.readBounded(StringReader(body), 100))
    }

    @Test
    fun `empty source yields empty string`() {
        assertEquals("", BoundedReader.readBounded(StringReader(""), 100))
    }

    @Test
    fun `returns null when the source exceeds the limit`() {
        val body = "x".repeat(101)
        assertNull(BoundedReader.readBounded(StringReader(body), 100))
    }

    @Test
    fun `bounds a source far larger than the limit`() {
        // A ~1 MiB source must be rejected against a small limit without
        // reading it all into memory.
        val body = "y".repeat(1_048_576)
        assertNull(BoundedReader.readBounded(StringReader(body), 4096))
    }
}
