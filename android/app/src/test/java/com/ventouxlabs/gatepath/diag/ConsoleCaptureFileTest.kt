package com.ventouxlabs.gatepath.diag

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.lang.reflect.Modifier
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

    private fun entry(offsetMs: Long = 0, incidentId: Long? = null) = ConsoleCaptureEntry(
        level = "ERROR",
        sourceHost = "portal.example.com",
        lineNumber = 7,
        message = "hello",
        offsetMs = offsetMs,
        incidentId = incidentId,
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

    @Test
    fun `entries carrying an incident id round-trip it, and the result surfaces it`() {
        ConsoleCaptureFile.write(file, listOf(entry(offsetMs = 1, incidentId = 42), entry(offsetMs = 2, incidentId = 42)))

        val result = ConsoleCaptureFile.read(file)
        assertEquals(0, result.unreadable)
        assertEquals(listOf(42L, 42L), result.entries.map { it.incidentId })
        assertEquals(42L, result.incidentId)
    }

    @Test
    fun `a line in the old shape, with no incident_id key, decodes with a null incident id and still counts as readable`() {
        file.appendText(
            "{\"level\":\"LOG\",\"source_host\":\"trunc.example.com\",\"line_number\":1," +
                "\"message\":\"hi\",\"offset_ms\":5}\n",
            Charsets.UTF_8,
        )

        val result = ConsoleCaptureFile.read(file)
        assertEquals(0, result.unreadable)
        assertEquals(1, result.entries.size)
        assertEquals(null, result.entries.single().incidentId)
        assertEquals(null, result.incidentId)
    }

    /**
     * Drift guard, like IncidentEvidenceTest's: this type is serialised to an
     * on-disk file and reaches the shared bundle, so adding a field is a
     * deliberate decision. Note the asymmetry a future field-adder needs told:
     * level, sourceHost, lineNumber and message are rendered per entry (and
     * redacted as text); offsetMs is not rendered at all; incidentId reaches
     * the bundle only through the section header and the incident note, never
     * through the per-entry line.
     */
    @Test
    fun `field set is guarded`() {
        val declared = ConsoleCaptureEntry::class.java.declaredFields
            .filterNot { Modifier.isStatic(it.modifiers) }.map { it.name }.toSet()
        assertEquals(
            "ConsoleCaptureEntry fields changed. The type is written to files/webview-console.jsonl " +
                "and rendered into the shared diagnostics bundle: decide whether the new field is " +
                "rendered per entry (then it goes through redactConsoleText), only in the header " +
                "(like incidentId), or not at all (like offsetMs), and update DiagnosticsBundle " +
                "and this guard together.",
            setOf("level", "sourceHost", "lineNumber", "message", "offsetMs", "incidentId"),
            declared,
        )
    }

    @Test
    fun `read result's incident id is null when no entry carries one`() {
        ConsoleCaptureFile.write(file, listOf(entry(offsetMs = 1, incidentId = null)))

        val result = ConsoleCaptureFile.read(file)
        assertEquals(null, result.incidentId)
    }
}
