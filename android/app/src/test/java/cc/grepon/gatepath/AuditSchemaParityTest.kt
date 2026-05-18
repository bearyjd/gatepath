package cc.grepon.gatepath

import cc.grepon.gatepath.audit.AuditEntry
import cc.grepon.gatepath.audit.AuditLogWriter
import cc.grepon.gatepath.session.CloseReason
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

/**
 * Schema parity tests — assert the Android writer's JSONL output conforms to
 * `docs/audit_log_schema.json` (the machine-readable contract shared with the
 * desktop app). If this test fails, the two platforms have drifted and a
 * cross-platform reader will break.
 */
class AuditSchemaParityTest {

    private lateinit var tempDir: File
    private lateinit var logFile: File
    private lateinit var writer: AuditLogWriter
    private lateinit var schema: JsonObject

    @Before
    fun setUp() {
        tempDir = Files.createTempDirectory("gatepath-schema-test").toFile()
        logFile = File(tempDir, "audit.jsonl")
        writer = AuditLogWriter(logFile)
        schema = loadSchema()
    }

    @After
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    private fun sampleEntry(closeReason: String = "portal_completed") = AuditEntry(
        schemaVersion = 1,
        timestampUtc = "2026-05-06T12:34:56.000Z",
        platform = "android",
        ssid = "Airport-WiFi",
        gatewayIp = "192.168.0.1",
        portalDomain = "wifi.example-airport.com",
        vpnInterfacesDetected = listOf("tailscale0 (full_tunnel)"),
        vpnWarningShown = true,
        sessionOpenedUtc = "2026-05-06T12:34:00.000Z",
        sessionClosedUtc = "2026-05-06T12:36:42.000Z",
        closeReason = closeReason,
        durationSeconds = 162,
        blockedNavigationAttempts = 2,
        blockedResourceRequests = 11,
    )

    private fun readWrittenJson(): JsonObject {
        val line = logFile.readText().trim().lines().first()
        return Json.parseToJsonElement(line).jsonObject
    }

    // ── Required-fields parity ──────────────────────────────────────────────

    @Test
    fun `writer emits every required field from schema`() = runBlocking {
        writer.append(sampleEntry())
        val obj = readWrittenJson()
        val required = schema["required_fields"]!!.jsonArray
            .map { it.jsonPrimitive.content }
            .toSet()
        val missing = required - obj.keys
        assertTrue("Writer omitted required fields: $missing", missing.isEmpty())
    }

    @Test
    fun `writer emits no fields outside the schema`() = runBlocking {
        writer.append(sampleEntry())
        val obj = readWrittenJson()
        val required = schema["required_fields"]!!.jsonArray
            .map { it.jsonPrimitive.content }
            .toSet()
        val extras = obj.keys - required
        assertTrue("Writer emitted unknown fields: $extras", extras.isEmpty())
    }

    // ── Enum parity ─────────────────────────────────────────────────────────

    @Test
    fun `every CloseReason maps to a value in the schema enum`() {
        val schemaEnum = schema["close_reason_enum"]!!.jsonArray
            .map { it.jsonPrimitive.content }
            .toSet()
        for (reason in CloseReason.entries) {
            assertTrue(
                "CloseReason.${reason.name} schemaValue '${reason.schemaValue}' " +
                    "is not in docs/audit_log_schema.json close_reason_enum=$schemaEnum",
                reason.schemaValue in schemaEnum,
            )
        }
    }

    @Test
    fun `aborted_pre_active is a valid close_reason`() = runBlocking {
        writer.append(sampleEntry(closeReason = "aborted_pre_active"))
        val obj = readWrittenJson()
        assertEquals("aborted_pre_active", obj["close_reason"]!!.jsonPrimitive.content)
    }

    @Test
    fun `aborted_pre_active accepts empty portal_domain (cross-platform parity)`() = runBlocking {
        // Mirrors desktop's permission for empty portal_domain when the session
        // never observed a portal URL (Monitoring-phase dismissal). The schema
        // doc explicitly allows this for ABORTED_PRE_ACTIVE only.
        val entry = sampleEntry(closeReason = "aborted_pre_active").copy(portalDomain = "")
        writer.append(entry)
        val obj = readWrittenJson()
        assertEquals("aborted_pre_active", obj["close_reason"]!!.jsonPrimitive.content)
        assertEquals("", obj["portal_domain"]!!.jsonPrimitive.content)
    }

    @Test
    fun `platform value is in the schema platform_enum`() = runBlocking {
        writer.append(sampleEntry())
        val obj = readWrittenJson()
        val platformEnum = schema["platform_enum"]!!.jsonArray
            .map { it.jsonPrimitive.content }
            .toSet()
        assertTrue(
            "Platform '${obj["platform"]}' not in $platformEnum",
            obj["platform"]!!.jsonPrimitive.content in platformEnum,
        )
    }

    // ── Field-type parity ───────────────────────────────────────────────────

    @Test
    fun `each field's emitted type matches the schema field_types`() = runBlocking {
        writer.append(sampleEntry())
        val obj = readWrittenJson()
        val fieldTypes = schema["field_types"]!!.jsonObject
        for ((field, declaredType) in fieldTypes) {
            val value = obj[field]
            assertNotNull("missing field $field", value)
            assertTrue(
                "field $field: declared $declaredType, got ${value!!::class.simpleName}=$value",
                matchesType(value, declaredType.jsonPrimitive.content),
            )
        }
    }

    private fun matchesType(value: JsonElement, declaredType: String): Boolean {
        if (value !is JsonPrimitive) {
            return when (declaredType) {
                "array<string>" -> value is JsonArray &&
                    value.all { it is JsonPrimitive && it.isString }
                else -> false
            }
        }
        // JSON has no separate int/bool type; both serialize as primitive content.
        // Strict mode: rely on `isString` for string-vs-non-string, then on the
        // raw content for int-vs-bool to avoid kotlinx coercion (true→1, 1→true).
        val raw = value.contentOrNull
        return when (declaredType) {
            "int" -> !value.isString && raw != null &&
                raw != "true" && raw != "false" &&
                raw.toIntOrNull() != null
            "string" -> value.isString
            "bool" -> !value.isString && (raw == "true" || raw == "false")
            "string|null" -> raw == null || value.isString
            "array<string>" -> false
            else -> false
        }
    }

    // ── Schema location ─────────────────────────────────────────────────────

    private fun loadSchema(): JsonObject {
        val explicit = System.getProperty("gatepath.repo.root")
        val root = if (explicit != null) File(explicit) else findRepoRoot()
        val file = File(root, "docs/audit_log_schema.json")
        require(file.exists()) {
            "audit_log_schema.json not found at $file " +
                "(set -Dgatepath.repo.root=<repo> or run from android/)"
        }
        return Json.parseToJsonElement(file.readText()).jsonObject
    }

    private fun findRepoRoot(): File {
        var dir = File(System.getProperty("user.dir") ?: ".")
        repeat(5) {
            if (File(dir, "docs/audit_log_schema.json").exists()) return dir
            dir = dir.parentFile ?: return dir
        }
        return dir
    }
}
