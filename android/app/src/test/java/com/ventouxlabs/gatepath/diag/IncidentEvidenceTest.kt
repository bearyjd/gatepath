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
    fun `absent evidence is stated in prose`() {
        val out = DiagnosticsBundle.build(meta, emptyList(), null, evidence = null, redact = true)
        assertTrue(out.contains("(no incident evidence captured)"))
    }
}
