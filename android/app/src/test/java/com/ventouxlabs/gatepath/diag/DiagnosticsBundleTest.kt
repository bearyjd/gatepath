package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.audit.AuditEntry
import com.ventouxlabs.gatepath.network.PortalProbeCapture
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Modifier

/**
 * Pure-JVM tests for [DiagnosticsBundle] — the shareable-bundle builder and its
 * redaction. No Android SDK: runs under run-jvm-tests.sh alongside the other
 * diag/audit tests.
 *
 * The redaction contract mirrors the desktop
 * `gatepath-netns-helper/packaging/collect-diagnostics.sh --redact`
 * (ssid, gateway_ip, portal_domain).
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
            checks = listOf(
                ProbeCheck("https", DiagnosticReport.HttpsOnlyCaptive("TLS handshake failed to portal.example.com")),
            ),
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
            checks = listOf(
                ProbeCheck(
                    "dns",
                    DiagnosticReport.DnsHijack("connectivitycheck.gstatic.example", "10.0.0.7", "93.184.216.34"),
                ),
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
            checks = listOf(ProbeCheck("dns", DiagnosticReport.DnsHijack("host.example", "10.0.0.7", "93.184.216.34"))),
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
            checks = listOf(
                ProbeCheck("vpn", DiagnosticReport.VpnBlocking(interfaceName = "tun0", isFullTunnel = true)),
                ProbeCheck("ok", DiagnosticReport.Healthy),
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

    @Test
    fun `probe capture exports structural evidence but never a portal url or body`() {
        val capture = captureOf(
            bodySha256 = PortalProbeCapture.sha256(
                "<script>location='http://portal.example/?token=secret'</script>",
            ),
        )
        val out = DiagnosticsBundle.build(
            meta,
            entries = emptyList(),
            diagnosis = null,
            probeCapture = capture,
            redact = true,
        )

        assertTrue(out.contains("http_status: 200"))
        assertTrue(out.contains("redirect_signal: SCRIPTED_LOCATION"))
        assertFalse(out.contains("portal.example"))
        assertFalse(out.contains("token=secret"))
    }

    /**
     * The digest is a stable fingerprint of a portal page that may be
     * personalised, so a shareable bundle must not carry it — but an
     * unredacted bundle still should, because comparing digests across probes
     * is the point of capturing one.
     */
    @Test
    fun `body digest is dropped when redacting and kept when not`() {
        val digest = PortalProbeCapture.sha256("<html>session=abc123</html>")
        val capture = captureOf(bodySha256 = digest)

        val redacted = DiagnosticsBundle.build(
            meta,
            entries = emptyList(),
            diagnosis = null,
            probeCapture = capture,
            redact = true,
        )
        val plain = DiagnosticsBundle.build(
            meta,
            entries = emptyList(),
            diagnosis = null,
            probeCapture = capture,
            redact = false,
        )

        assertFalse(redacted.contains(digest))
        assertTrue(redacted.contains("body_sha256: ${DiagnosticsBundle.REDACTED}"))
        assertTrue(plain.contains(digest))
    }

    /**
     * A `Content-Type` is arbitrary gateway-controlled text, so a hostile
     * gateway must not be able to route a per-device value into a bundle the
     * user shares by hiding it in a header parameter.
     */
    @Test
    fun `a content-type carrying a session parameter cannot reach the bundle`() {
        val hostile = PortalProbeCapture.normalizeContentType("text/html; session=abc123device")
        val out = DiagnosticsBundle.build(
            meta,
            entries = emptyList(),
            diagnosis = null,
            probeCapture = captureOf(contentType = hostile),
            redact = true,
        )

        assertEquals("text/html", hostile)
        assertFalse(out.contains("abc123device"))
        assertFalse(out.contains("session="))
    }

    /**
     * Drift guard on what the probe capture is allowed to export.
     *
     * Every field here reaches a bundle the user shares off-device. Only
     * [PortalProbeCapture.bodySha256] is scrubbed when `redact = true`; the
     * rest are exported in both modes and so must be non-identifying *by
     * construction* — a contract the class upholds at capture time, not
     * something the renderer can enforce after the fact.
     *
     * Asserting the field set makes adding one a deliberate decision: show it
     * cannot carry a device identifier or credential and list it here, or give
     * it a branch in `renderProbeCapture`. An earlier version of this guard
     * listed `contentType` as safe because it came from the gateway; that
     * missed the adversary redaction exists for, which is whoever the user
     * hands the bundle to.
     */
    @Test
    fun `every exported probe-capture field is privacy-safe by construction`() {
        val declared = PortalProbeCapture::class.java.declaredFields
            // A companion object contributes a static `Companion` field.
            .filterNot { Modifier.isStatic(it.modifiers) }
            .map { it.name }
            .toSet()

        val knownPrivacySafe = setOf(
            "httpStatus", // numeric status code
            "contentType", // narrowed to a known media type by normalizeContentType
            "redirectSignal", // enum over a closed set
            "bodyCharacters", // length only, never the body
            "bodySha256", // one-way digest, and dropped entirely when redacting
        )

        assertEquals(
            "PortalProbeCapture's fields changed. Every field except bodySha256 is " +
                "written into the shared diagnostics bundle unredacted, in both modes. " +
                "Confirm the new field cannot carry a device identifier, credential, or " +
                "portal URL and add it here — or give it a branch in " +
                "DiagnosticsBundle.renderProbeCapture.",
            knownPrivacySafe,
            declared,
        )
    }

    /** A capture whose fields are all unremarkable, so a test varies only what it is about. */
    private fun captureOf(
        contentType: String? = "text/html",
        bodySha256: String? = null,
    ) = PortalProbeCapture(
        httpStatus = 200,
        contentType = contentType,
        redirectSignal = PortalProbeCapture.RedirectSignal.SCRIPTED_LOCATION,
        bodyCharacters = 91,
        bodySha256 = bodySha256,
    )
}
