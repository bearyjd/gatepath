package cc.grepon.gatepath.diag

import cc.grepon.gatepath.audit.AuditEntry
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM tests for [DiagnosticsBundle] — the shareable-bundle builder and its
 * redaction. No Android SDK: runs under run-jvm-tests.sh alongside the other
 * diag/audit tests.
 *
 * The redaction contract mirrors the desktop
 * `packaging/collect-diagnostics.sh --redact` (ssid, gateway_ip, portal_domain).
 */
class DiagnosticsBundleTest {

    private val meta = BundleMeta(
        generatedUtc = "2026-07-01T00:00:00Z",
        appVersionName = "1.0.0",
        appVersionCode = 1,
        androidRelease = "14",
        androidSdkInt = 34,
    )

    private fun entry(
        ssid: String? = "HomeWiFi-5G",
        gatewayIp: String? = "192.168.1.1",
        portalDomain: String = "portal.example.com",
    ) = AuditEntry(
        timestampUtc = "2026-07-01T00:01:00Z",
        ssid = ssid,
        gatewayIp = gatewayIp,
        portalDomain = portalDomain,
        vpnInterfacesDetected = emptyList(),
        vpnWarningShown = false,
        sessionOpenedUtc = "2026-07-01T00:00:00Z",
        sessionClosedUtc = "2026-07-01T00:01:00Z",
        closeReason = "portal_completed",
        durationSeconds = 60,
        blockedNavigationAttempts = 0,
        blockedResourceRequests = 0,
    )

    @Test
    fun `redact removes wifi name, gateway ip and portal domain`() {
        val out = DiagnosticsBundle.build(meta, listOf(entry()), diagnosis = null, redact = true)

        assertFalse("SSID must not leak", out.contains("HomeWiFi-5G"))
        assertFalse("gateway IP must not leak", out.contains("192.168.1.1"))
        assertFalse("portal domain must not leak", out.contains("portal.example.com"))
        assertTrue("redacted fields marked", out.contains("REDACTED"))
    }

    @Test
    fun `no redact preserves the raw identifiers`() {
        val out = DiagnosticsBundle.build(meta, listOf(entry()), diagnosis = null, redact = false)

        assertTrue(out.contains("HomeWiFi-5G"))
        assertTrue(out.contains("192.168.1.1"))
        assertTrue(out.contains("portal.example.com"))
        assertFalse("nothing redacted when redact=false", out.contains("REDACTED"))
    }

    @Test
    fun `null identifiers stay null under redaction`() {
        val out = DiagnosticsBundle.build(
            meta,
            listOf(entry(ssid = null, gatewayIp = null)),
            diagnosis = null,
            redact = true,
        )

        // A null identifier has nothing to reveal, so it is left as null — this
        // matches the desktop sed pattern, which only rewrites quoted values.
        assertTrue(out.contains("\"ssid\":null"))
        assertTrue(out.contains("\"gateway_ip\":null"))
        // portal_domain is always a string, so it is always redacted.
        assertTrue(out.contains("\"portal_domain\":\"REDACTED\""))
    }

    @Test
    fun `audit lines round-trip as valid audit json`() {
        val json = Json { encodeDefaults = true }
        val out = DiagnosticsBundle.build(
            meta,
            listOf(entry(), entry(ssid = "Cafe-Guest")),
            diagnosis = null,
            redact = false,
        )

        val jsonLines = out.lineSequence().filter { it.trimStart().startsWith("{") }.toList()
        assertEquals("one line per audit entry", 2, jsonLines.size)
        // Each line must decode back into an AuditEntry (schema-faithful).
        jsonLines.forEach { line -> json.decodeFromString<AuditEntry>(line) }
    }

    @Test
    fun `header carries app and platform metadata`() {
        val out = DiagnosticsBundle.build(meta, entries = emptyList(), diagnosis = null, redact = false)

        assertTrue(out.contains("1.0.0"))
        assertTrue(out.contains("API 34"))
        assertTrue(out.contains("2026-07-01T00:00:00Z"))
        assertTrue("empty log is stated explicitly", out.contains("(no entries)"))
    }

    @Test
    fun `redact scrubs a portal domain echoed in the diagnosis text`() {
        // A probe error can embed the portal domain (e.g. UnknownHostException);
        // redaction must catch it there too, not only in the audit line.
        val diagnosis = DiagnosisResult(
            top = DiagnosticReport.HttpsOnlyCaptive("TLS handshake failed to portal.example.com"),
            all = listOf(DiagnosticReport.HttpsOnlyCaptive("TLS handshake failed to portal.example.com")),
            recommended = RecommendedAction.NoActionAvailable,
        )
        val out = DiagnosticsBundle.build(meta, listOf(entry()), diagnosis, redact = true)

        assertFalse("portal domain must not leak via the diagnosis text", out.contains("portal.example.com"))
    }

    @Test
    fun `redact masks ip literals in the diagnosis text`() {
        val diagnosis = DiagnosisResult(
            top = DiagnosticReport.DnsHijack(
                hostProbed = "connectivitycheck.gstatic.example",
                systemAnswer = "10.0.0.7",
                doHAnswer = "93.184.216.34",
            ),
            all = listOf(
                DiagnosticReport.DnsHijack("connectivitycheck.gstatic.example", "10.0.0.7", "93.184.216.34"),
            ),
            recommended = RecommendedAction.NoActionAvailable,
        )
        val out = DiagnosticsBundle.build(meta, entries = emptyList(), diagnosis = diagnosis, redact = true)

        assertFalse("gateway/DNS IP must not leak", out.contains("10.0.0.7"))
        assertFalse("resolver IP must not leak", out.contains("93.184.216.34"))
    }

    @Test
    fun `no redact keeps the diagnosis ip literals intact`() {
        val diagnosis = DiagnosisResult(
            top = DiagnosticReport.DnsHijack("host.example", "10.0.0.7", "93.184.216.34"),
            all = listOf(DiagnosticReport.DnsHijack("host.example", "10.0.0.7", "93.184.216.34")),
            recommended = RecommendedAction.NoActionAvailable,
        )
        val out = DiagnosticsBundle.build(meta, entries = emptyList(), diagnosis = diagnosis, redact = false)

        assertTrue(out.contains("10.0.0.7"))
        assertTrue(out.contains("93.184.216.34"))
    }

    @Test
    fun `latest diagnosis is rendered when present`() {
        val diagnosis = DiagnosisResult(
            top = DiagnosticReport.VpnBlocking(interfaceName = "tun0", isFullTunnel = true),
            all = listOf(
                DiagnosticReport.VpnBlocking(interfaceName = "tun0", isFullTunnel = true),
                DiagnosticReport.Healthy,
            ),
            recommended = RecommendedAction.UserAction(RecommendedAction.PAUSE_VPN, "Pause your VPN"),
        )

        val out = DiagnosticsBundle.build(meta, entries = emptyList(), diagnosis = diagnosis, redact = false)

        assertTrue(out.contains("VpnBlocking"))
        assertTrue(out.contains("tun0"))
        assertTrue(out.contains("Pause your VPN"))
    }

    @Test
    fun `absent diagnosis is stated, not omitted`() {
        val out = DiagnosticsBundle.build(meta, entries = emptyList(), diagnosis = null, redact = false)
        assertTrue(out.contains("no diagnosis captured"))
    }
}
