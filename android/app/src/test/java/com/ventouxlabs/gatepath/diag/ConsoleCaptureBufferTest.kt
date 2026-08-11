package com.ventouxlabs.gatepath.diag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConsoleCaptureBufferTest {

    private fun entry(message: String = "hello", offsetMs: Long = 0) = ConsoleCaptureEntry(
        level = "LOG",
        sourceHost = "portal.example.com",
        lineNumber = 42,
        message = message,
        offsetMs = offsetMs,
    )

    @Test
    fun `snapshot returns entries in record order`() {
        val buffer = ConsoleCaptureBuffer(capacity = 10)
        buffer.record(entry(offsetMs = 1))
        buffer.record(entry(offsetMs = 2))
        buffer.record(entry(offsetMs = 3))

        val snapshot = buffer.snapshot()
        assertEquals(listOf(1L, 2L, 3L), snapshot.map { it.offsetMs })
    }

    @Test
    fun `oldest entry is evicted once capacity is reached`() {
        val buffer = ConsoleCaptureBuffer(capacity = 3)
        buffer.record(entry(offsetMs = 1))
        buffer.record(entry(offsetMs = 2))
        buffer.record(entry(offsetMs = 3))
        buffer.record(entry(offsetMs = 4))

        val snapshot = buffer.snapshot()
        assertEquals(3, snapshot.size)
        assertEquals(listOf(2L, 3L, 4L), snapshot.map { it.offsetMs })
    }

    @Test
    fun `message longer than the cap is truncated with an ellipsis`() {
        val buffer = ConsoleCaptureBuffer()
        val longMessage = "x".repeat(600)
        buffer.record(entry(message = longMessage))

        val recorded = buffer.snapshot().single().message
        assertEquals(ConsoleCaptureBuffer.MAX_MESSAGE_CHARS + 1, recorded.length) // +1 for the ellipsis char
        assertTrue(recorded.endsWith("…"))
    }

    @Test
    fun `message at or under the cap is not truncated`() {
        val buffer = ConsoleCaptureBuffer()
        val message = "x".repeat(ConsoleCaptureBuffer.MAX_MESSAGE_CHARS)
        buffer.record(entry(message = message))

        assertEquals(message, buffer.snapshot().single().message)
    }

    @Test
    fun `empty buffer snapshots to an empty list`() {
        assertTrue(ConsoleCaptureBuffer().snapshot().isEmpty())
    }

    @Test
    fun `clear empties the buffer`() {
        val buffer = ConsoleCaptureBuffer()
        buffer.record(entry())
        buffer.clear()

        assertTrue(buffer.snapshot().isEmpty())
    }
}
