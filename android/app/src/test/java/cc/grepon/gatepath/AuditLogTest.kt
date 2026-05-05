package cc.grepon.gatepath

import cc.grepon.gatepath.audit.AuditEntry
import cc.grepon.gatepath.audit.AuditLogWriter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

/**
 * Pure-JVM tests for [AuditLogWriter]. No Android SDK required.
 * Verifies schema compliance, round-trip accuracy, concurrent writes, and read order.
 */
class AuditLogTest {

    private lateinit var tempDir: File
    private lateinit var logFile: File
    private lateinit var writer: AuditLogWriter

    @Before
    fun setUp() {
        tempDir = Files.createTempDirectory("gatepath-audit-test").toFile()
        logFile = File(tempDir, "audit.jsonl")
        writer = AuditLogWriter(logFile)
    }

    @After
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    private fun sampleEntry(
        index: Int = 0,
        closeReason: String = "portal_completed",
        ssid: String? = "Airport-WiFi",
        gatewayIp: String? = "192.168.0.1",
        sessionClosedUtc: String? = "2026-05-05T12:36:42.000Z",
    ) = AuditEntry(
        schemaVersion = 1,
        timestampUtc = "2026-05-05T12:34:56.00${index}Z",
        platform = "android",
        ssid = ssid,
        gatewayIp = gatewayIp,
        portalDomain = "wifi.example-airport.com",
        vpnInterfacesDetected = listOf("tailscale0 (full_tunnel)"),
        vpnWarningShown = true,
        sessionOpenedUtc = "2026-05-05T12:34:00.000Z",
        sessionClosedUtc = sessionClosedUtc,
        closeReason = closeReason,
        durationSeconds = 162,
        blockedNavigationAttempts = 2,
        blockedResourceRequests = 11,
    )

    // ── Schema compliance ───────────────────────────────────────────────────

    @Test
    fun `written entry round-trips with all schema fields present`() = runBlocking {
        val entry = sampleEntry()
        writer.append(entry)

        val entries = writer.readAll()
        assertEquals(1, entries.size)
        val read = entries[0]

        assertEquals(1, read.schemaVersion)
        assertEquals("android", read.platform)
        assertEquals("Airport-WiFi", read.ssid)
        assertEquals("192.168.0.1", read.gatewayIp)
        assertEquals("wifi.example-airport.com", read.portalDomain)
        assertEquals(listOf("tailscale0 (full_tunnel)"), read.vpnInterfacesDetected)
        assertTrue(read.vpnWarningShown)
        assertEquals("2026-05-05T12:34:00.000Z", read.sessionOpenedUtc)
        assertEquals("2026-05-05T12:36:42.000Z", read.sessionClosedUtc)
        assertEquals("portal_completed", read.closeReason)
        assertEquals(162, read.durationSeconds)
        assertEquals(2, read.blockedNavigationAttempts)
        assertEquals(11, read.blockedResourceRequests)
    }

    @Test
    fun `nullable ssid round-trips as null`() = runBlocking {
        writer.append(sampleEntry(ssid = null))
        val read = writer.readAll()[0]
        assertNull(read.ssid)
    }

    @Test
    fun `nullable gatewayIp round-trips as null`() = runBlocking {
        writer.append(sampleEntry(gatewayIp = null))
        val read = writer.readAll()[0]
        assertNull(read.gatewayIp)
    }

    @Test
    fun `nullable sessionClosedUtc round-trips as null`() = runBlocking {
        writer.append(sampleEntry(sessionClosedUtc = null))
        val read = writer.readAll()[0]
        assertNull(read.sessionClosedUtc)
    }

    @Test
    fun `all close_reason enum values are valid strings`() = runBlocking {
        val validReasons = setOf("portal_completed", "user_dismissed", "timeout", "error")
        for (reason in validReasons) {
            writer.append(sampleEntry(closeReason = reason))
        }
        val entries = writer.readAll()
        val writtenReasons = entries.map { it.closeReason }.toSet()
        assertEquals(validReasons, writtenReasons)
    }

    // ── Concurrent writes ───────────────────────────────────────────────────

    @Test
    fun `10 concurrent writes produce 10 valid lines`() = runBlocking {
        val jobs = (0 until 10).map { i ->
            async(Dispatchers.IO) {
                writer.append(sampleEntry(index = i))
            }
        }
        jobs.awaitAll()

        val entries = writer.readAll()
        assertEquals(10, entries.size)
        entries.forEach { entry ->
            assertEquals(1, entry.schemaVersion)
            assertEquals("android", entry.platform)
            assertNotNull(entry.timestampUtc)
        }
    }

    // ── Read order ──────────────────────────────────────────────────────────

    @Test
    fun `entries read in chronological file order`() = runBlocking {
        for (i in 0 until 5) {
            writer.append(sampleEntry(index = i))
        }
        val entries = writer.readAll()
        assertEquals(5, entries.size)
        // Verify order preserved (timestamps differ by index digit)
        for (i in 0 until 5) {
            assertTrue(entries[i].timestampUtc.contains("00${i}Z"))
        }
    }

    @Test
    fun `readAll returns empty list when file does not exist`() {
        val nonExistentFile = File(tempDir, "missing.jsonl")
        val emptyWriter = AuditLogWriter(nonExistentFile)
        val entries = emptyWriter.readAll()
        assertTrue(entries.isEmpty())
    }
}
