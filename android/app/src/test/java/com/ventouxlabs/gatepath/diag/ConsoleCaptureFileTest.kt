package com.ventouxlabs.gatepath.diag

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

class ConsoleCaptureFileTest {

    private lateinit var tempDir: File
    private lateinit var file: File

    @Before
    fun setUp() {
        tempDir = Files.createTempDirectory("gatepath-console-capture-test").toFile()
        file = File(tempDir, CONSOLE_CAPTURE_FILE_NAME)
    }

    @After
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    private fun entry(offsetMs: Long = 0) = ConsoleCaptureEntry(
        level = "ERROR",
        sourceHost = "portal.example.com",
        lineNumber = 7,
        message = "hello",
        offsetMs = offsetMs,
    )

    @Test
    fun `written entries round-trip in order`() {
        ConsoleCaptureFile.write(file, listOf(entry(offsetMs = 1), entry(offsetMs = 2)))

        val result = ConsoleCaptureFile.read(file)
        assertEquals(0, result.unreadable)
        assertEquals(listOf(1L, 2L), result.entries.map { it.offsetMs })
    }

    @Test
    fun `write overwrites a previous session rather than appending`() {
        ConsoleCaptureFile.write(file, listOf(entry(offsetMs = 1)))
        ConsoleCaptureFile.write(file, listOf(entry(offsetMs = 2), entry(offsetMs = 3)))

        val result = ConsoleCaptureFile.read(file)
        assertEquals(listOf(2L, 3L), result.entries.map { it.offsetMs })
    }

    @Test
    fun `read returns an empty result when the file does not exist`() {
        val missing = File(tempDir, "missing.jsonl")
        val result = ConsoleCaptureFile.read(missing)
        assertTrue(result.entries.isEmpty())
        assertEquals(0, result.unreadable)
    }

    @Test
    fun `read counts an unreadable line instead of losing the whole file`() {
        ConsoleCaptureFile.write(file, listOf(entry(offsetMs = 1)))
        file.appendText("{\"level\":\"LOG\",\"source_host\":\"trunc\n", Charsets.UTF_8)

        val result = ConsoleCaptureFile.read(file)
        assertEquals(1, result.unreadable)
        assertEquals(1, result.entries.size)
    }

    @Test
    fun `write with an empty list produces an empty, readable file`() {
        ConsoleCaptureFile.write(file, emptyList())

        val result = ConsoleCaptureFile.read(file)
        assertTrue(result.entries.isEmpty())
        assertEquals(0, result.unreadable)
    }
}
