package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.PortalProbeCapture
import com.ventouxlabs.gatepath.network.ProbePath
import com.ventouxlabs.gatepath.network.VpnKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Modifier

class IncidentEvidenceTest {

    private val meta = BundleMeta("2026-09-19T00:00:00Z", "1.1.0", 3, "16", 36)

    private fun evidence(bindError: String? = null) = IncidentEvidence(
        confinement = "tunnelled",
        probePath = ProbePath.BOUND_WIFI,
        probeCapture = PortalProbeCapture.of(302, "text/html", PortalProbeCapture.RedirectSignal.LOCATION_HEADER),
        resolverWifi = listOf("10.0.0.1"),
        resolverDoh = listOf("93.184.216.34"),
        certSummary = CertSummary.of(3, 1L, 2L, true, byteArrayOf(9)),
        vpnKind = VpnKind.TAILSCALE,
        vpnInterfaces = listOf("tailscale0 (split_tunnel)"),
        privateDnsStrict = false,
        bindError = bindError,
        fallbackError = null,
    )

    @Test
    fun `field set is guarded`() {
        val declared = IncidentEvidence::class.java.declaredFields
            .filterNot { Modifier.isStatic(it.modifiers) }.map { it.name }.toSet()
        assertEquals(
            "IncidentEvidence fields changed. Every field is exported in the shared bundle; " +
                "a new one must be an enum, number, boolean, date, fingerprint, or pass through redaction.",
            setOf(
                "confinement", "probePath", "probeCapture", "resolverWifi", "resolverDoh",
                "certSummary", "vpnKind", "vpnInterfaces", "privateDnsStrict", "bindError", "fallbackError",
            ),
            declared,
        )
    }

    @Test
    fun `bundle renders the evidence section with the state and path`() {
        val out = DiagnosticsBundle.build(meta, emptyList(), null, evidence = evidence(), redact = false)
        assertTrue(out.contains("--- Incident evidence ---"))
        assertTrue(out.contains("confinement: tunnelled"))
        assertTrue(out.contains("probe_path: BOUND_WIFI"))
        assertTrue(out.contains("vpn_kind: TAILSCALE"))
        assertTrue(out.contains("cert_self_signed: true"))
        assertTrue(out.contains("probe_http_status: 302"))
        assertTrue(out.contains("probe_redirect_signal: LOCATION_HEADER"))
    }

    @Test
    fun `redaction masks resolver answers and error text ip literals`() {
        val out = DiagnosticsBundle.build(
            meta, emptyList(), null,
            evidence = evidence(bindError = "connect to /10.0.0.1:80 failed: EPERM"), redact = true,
        )
        assertFalse(out.contains("10.0.0.1"))
        assertFalse(out.contains("93.184.216.34"))
        assertTrue(out.contains("EPERM"))
    }

    @Test
    fun `redaction masks ipv6 resolver answers and error literals`() {
        val out = DiagnosticsBundle.build(
            meta, emptyList(), null,
            evidence = evidence(bindError = "connect to [fe80::1]:80 failed: EPERM").copy(
                resolverWifi = listOf("fe80::1"),
                resolverDoh = listOf("2001:db8::1", "2001:0db8:85a3:0000:0000:8a2e:0370:7334"),
            ),
            redact = true,
        )
        assertFalse(out.contains("fe80::1"))
        assertFalse(out.contains("2001:db8::1"))
        assertFalse(out.contains("2001:0db8:85a3:0000:0000:8a2e:0370:7334"))
        assertTrue(out.contains("REDACTED"))
    }

    @Test
    fun `redaction leaves timestamps and port-like text alone`() {
        val out = DiagnosticsBundle.build(
            meta, emptyList(), null,
            evidence = evidence(bindError = "at 2026-09-19T00:00:00Z port 80:443 failed"), redact = true,
        )
        assertTrue(out.contains("2026-09-19T00:00:00Z"))
        assertTrue(out.contains("80:443"))
    }

    @Test
    fun `absent evidence is stated in prose`() {
        val out = DiagnosticsBundle.build(meta, emptyList(), null, evidence = null, redact = true)
        assertTrue(out.contains("(no incident evidence captured)"))
    }

    @Test
    fun `redaction replaces cert fingerprint and validity epochs but keeps error code and self-signed`() {
        val summary = CertSummary.of(3, 1_700_000_000_000L, 1_700_003_600_000L, true, byteArrayOf(9, 1, 2, 3))
        val out = DiagnosticsBundle.build(
            meta, emptyList(), null,
            evidence = evidence().copy(certSummary = summary),
            redact = true,
        )
        assertFalse(out.contains("1700000000000"))
        assertFalse(out.contains("1700003600000"))
        assertFalse("the fingerprint hex must not leak", out.contains(summary.sha256Fingerprint))
        assertTrue(out.contains("cert_primary_error: 3"))
        assertTrue(out.contains("cert_self_signed: true"))
        assertTrue(out.contains("cert_not_before_epoch_ms: REDACTED"))
        assertTrue(out.contains("cert_not_after_epoch_ms: REDACTED"))
        assertTrue(out.contains("cert_sha256: REDACTED"))
    }

    @Test
    fun `no redaction shows cert fingerprint and validity epochs verbatim`() {
        val summary = CertSummary.of(3, 1_700_000_000_000L, 1_700_003_600_000L, true, byteArrayOf(9, 1, 2, 3))
        val out = DiagnosticsBundle.build(
            meta, emptyList(), null,
            evidence = evidence().copy(certSummary = summary),
            redact = false,
        )
        assertTrue(out.contains("cert_primary_error: 3"))
        assertTrue(out.contains("cert_self_signed: true"))
        assertTrue(out.contains("cert_not_before_epoch_ms: 1700000000000"))
        assertTrue(out.contains("cert_not_after_epoch_ms: 1700003600000"))
        assertTrue(out.contains("cert_sha256: ${summary.sha256Fingerprint}"))
    }

    @Test
    fun `a null cert summary still reads as prose regardless of redaction`() {
        val noCert = evidence().copy(certSummary = null)
        val redacted = DiagnosticsBundle.build(meta, emptyList(), null, evidence = noCert, redact = true)
        val plain = DiagnosticsBundle.build(meta, emptyList(), null, evidence = noCert, redact = false)
        assertTrue(redacted.contains("(no certificate error observed)"))
        assertTrue(plain.contains("(no certificate error observed)"))
    }

    @Test
    fun `redaction scrubs a non-literal resolver answer and its echo in free text with no audit entries`() {
        // Tunnelled/Blocked/DnsStrict/Unknown incidents never open a session, so
        // they never write an audit entry — `entries` is empty here on purpose.
        // The resolver fields carry IP literals today, which the unconditional
        // IP pass masks regardless; this pins the defence-in-depth contract
        // that a non-literal value in them (which no production path produces
        // yet) is harvested and scrubbed rather than left to that pass.
        val hostname = "venue-hijack.example.net"
        val out = DiagnosticsBundle.build(
            meta, emptyList(), null,
            evidence = evidence(bindError = "connect to $hostname failed: EPERM").copy(
                resolverWifi = listOf(hostname),
            ),
            redact = true,
        )
        assertFalse("resolver-answer hostname must not leak", out.contains(hostname))
        assertTrue("unrelated error text is untouched", out.contains("EPERM"))
    }

    @Test
    fun `redaction scrubs a non-literal doh resolver answer echoed in free text with no audit entries`() {
        // Same defence-in-depth contract as above, for the DoH answer field.
        val hostname = "sinkhole.captive-vendor.example"
        val out = DiagnosticsBundle.build(
            meta, emptyList(), null,
            evidence = evidence(bindError = null).copy(
                resolverDoh = listOf(hostname),
                fallbackError = "default route saw $hostname",
            ),
            redact = true,
        )
        assertFalse("doh resolver-answer hostname must not leak", out.contains(hostname))
    }
}
