package com.ventouxlabs.gatepath.session

import com.ventouxlabs.gatepath.diag.DiagnosisResult
import com.ventouxlabs.gatepath.diag.DiagnosticReport
import com.ventouxlabs.gatepath.diag.RecommendedAction
import com.ventouxlabs.gatepath.network.ClassificationInputs
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.NetworkDiagnostics
import com.ventouxlabs.gatepath.network.PortalProbeCapture
import com.ventouxlabs.gatepath.network.ProbeErrorReason
import com.ventouxlabs.gatepath.network.ProbePath
import com.ventouxlabs.gatepath.network.ProbeResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Uses [Net], a plain data class with real `equals`/`hashCode`, instead of
 * [android.net.Network]: under Gradle unit tests, `android.net.Network`'s
 * android.jar stub has a package-private constructor (so it cannot be
 * constructed here at all) and a stub `equals` that returns false even for
 * `a == a` (see CLAUDE.md's "two paths can disagree" note). [IncidentTracker]
 * is generic over the network type for exactly this reason.
 */
class IncidentTrackerTest {

    private data class Net(val id: Int)

    private fun diagnostics(
        bindError: String? = "EPERM",
        fallbackError: String? = null,
    ) = NetworkDiagnostics(
        networkId = "net-1",
        bindProbeError = bindError,
        fallbackProbeError = fallbackError,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        privateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        dnsServerCount = 1,
        hasValidatedCellular = false,
        defaultRouteBypassesCaptive = null,
    )

    private fun tunnelledInputs() = ClassificationInputs(
        bound = ProbeResult.Error("EPERM", ProbeErrorReason.PERMISSION_DENIED),
        fallback = null,
        vpnInterfaces = emptyList(),
        privateDnsStrict = false,
        portalHostResolvedOnWifi = null,
    )

    private fun portalInputs(url: String, capture: PortalProbeCapture? = null) = ClassificationInputs(
        bound = ProbeResult.Portal(url, capture),
        fallback = null,
        vpnInterfaces = emptyList(),
        privateDnsStrict = false,
        portalHostResolvedOnWifi = null,
    )

    @Test
    fun `begin publishes confinement and evidence and returns a fresh id each time`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        val begun1 = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        assertEquals(1L, begun1.id)
        assertEquals(begun1.confinement, tracker.confinement.value)
        assertEquals(begun1.evidence, tracker.evidence.value)
        assertEquals(network, tracker.suspectedNetwork)

        val begun2 = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        assertEquals(2L, begun2.id)
        assertNotEquals(begun1.id, begun2.id)
        assertEquals(begun2.confinement, tracker.confinement.value)
        assertEquals(begun2.evidence, tracker.evidence.value)
    }

    @Test
    fun `an update keyed to a previous id is dropped after a second begin`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        val begun1 = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        val currentEvidence = tracker.evidence.value

        tracker.updateEvidence(begun1.id) { it.copy(bindError = "stale write") }

        assertEquals(currentEvidence, tracker.evidence.value)
    }

    @Test
    fun `clearIf with a different network leaves everything`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)
        val other = Net(2)

        tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        val cleared = tracker.clearIf(other)

        assertFalse(cleared)
        assertNotNull(tracker.confinement.value)
        assertNotNull(tracker.evidence.value)
        assertEquals(network, tracker.suspectedNetwork)
    }

    @Test
    fun `clearIf with the matching network clears and a later keyed write is dropped`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        val begun = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        val cleared = tracker.clearIf(network)

        assertTrue(cleared)
        assertNull(tracker.confinement.value)
        assertNull(tracker.evidence.value)
        assertNull(tracker.diagnosis.value)
        assertNull(tracker.suspectedNetwork)
        assertNull(tracker.lastDiagnostics)

        tracker.setDiagnosis(begun.id, healthyDiagnosis())
        assertNull("a write keyed to the cleared incident must stay dropped", tracker.diagnosis.value)
    }

    @Test
    fun `clearIf with an equal but not identical network instance clears`() {
        // The production case: the same network arrives as different
        // instances from different callbacks (see NetworkBinder KDoc), so
        // clearIf must match on equals(), not on reference identity.
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        val equalButDistinct = Net(1)
        assertNotSame(network, equalButDistinct)

        val cleared = tracker.clearIf(equalButDistinct)

        assertTrue(cleared)
        assertNull(tracker.confinement.value)
        assertNull(tracker.suspectedNetwork)
    }

    @Test
    fun `a write keyed to the zero sentinel is dropped after clear, not resurrected`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        tracker.clear()
        assertEquals(0L, tracker.currentId)

        // 0L equals currentId right after clear; without the explicit
        // isLive() guard this would pass a bare `id == currentId` check.
        tracker.setDiagnosis(0L, healthyDiagnosis())
        assertNull("a write keyed to the zero sentinel must never publish", tracker.diagnosis.value)
    }

    @Test
    fun `adoptDefaultRouteCapture sets DEFAULT_ROUTE and does not overwrite an existing capture`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)
        val boundCapture = PortalProbeCapture.of(302, "text/html", PortalProbeCapture.RedirectSignal.LOCATION_HEADER)
        val freshCapture = PortalProbeCapture.of(200, "text/html", PortalProbeCapture.RedirectSignal.NONE)

        val withCapture = tracker.begin(
            network,
            portalInputs("http://10.0.0.1/login", boundCapture),
            ProbePath.BOUND_WIFI,
            diagnostics(),
        )
        assertEquals(boundCapture, tracker.evidence.value?.probeCapture)

        tracker.adoptDefaultRouteCapture(withCapture.id, freshCapture)
        assertEquals("must not overwrite an existing capture", boundCapture, tracker.evidence.value?.probeCapture)
        assertEquals("path stays as recorded when the capture is kept", ProbePath.BOUND_WIFI, tracker.evidence.value?.probePath)

        val withoutCapture = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        assertNull(tracker.evidence.value?.probeCapture)

        tracker.adoptDefaultRouteCapture(withoutCapture.id, freshCapture)
        assertEquals(freshCapture, tracker.evidence.value?.probeCapture)
        assertEquals(ProbePath.DEFAULT_ROUTE, tracker.evidence.value?.probePath)
    }

    @Test
    fun `setDiagnosis with a stale id is dropped`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        val begun1 = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())

        tracker.setDiagnosis(begun1.id, healthyDiagnosis())
        assertNull(tracker.diagnosis.value)
    }

    @Test
    fun `setDiagnosis with the current id publishes`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        val begun = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        val result = healthyDiagnosis()
        tracker.setDiagnosis(begun.id, result)

        assertEquals(result, tracker.diagnosis.value)
    }

    @Test
    fun `updateLastDiagnostics with a stale id is dropped`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        val begun1 = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        val currentSnapshot = tracker.lastDiagnostics

        tracker.updateLastDiagnostics(begun1.id, diagnostics(bindError = "stale rerun"))

        assertEquals(currentSnapshot, tracker.lastDiagnostics)
    }

    @Test
    fun `updateLastDiagnostics with the current id replaces lastDiagnostics`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        val begun = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        assertEquals(diagnostics(), tracker.lastDiagnostics)

        val fresh = diagnostics(bindError = null, fallbackError = "fresh rerun error")
        tracker.updateLastDiagnostics(begun.id, fresh)

        assertEquals(fresh, tracker.lastDiagnostics)
    }

    @Test
    fun `portalHost is derived from DnsStrict, from a Confined portal url, and null otherwise`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        val dnsStrictInputs = ClassificationInputs(
            bound = ProbeResult.Portal("https://n143.network-auth.com/splash", null),
            fallback = null,
            vpnInterfaces = emptyList(),
            privateDnsStrict = true,
            portalHostResolvedOnWifi = false,
        )
        val dnsStrictBegun = tracker.begin(network, dnsStrictInputs, ProbePath.BOUND_WIFI, diagnostics())
        assertTrue(dnsStrictBegun.confinement is ConfinementState.DnsStrict)
        assertEquals("n143.network-auth.com", tracker.evidence.value?.portalHost)

        val confinedBegun = tracker.begin(
            network,
            portalInputs("http://10.0.0.1/login"),
            ProbePath.BOUND_WIFI,
            diagnostics(),
        )
        assertTrue(confinedBegun.confinement is ConfinementState.Confined)
        assertEquals("10.0.0.1", tracker.evidence.value?.portalHost)

        val tunnelledBegun = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        assertTrue(tunnelledBegun.confinement is ConfinementState.Tunnelled)
        assertNull(tracker.evidence.value?.portalHost)
    }

    @Test
    fun `currentId tracks the live incident and resets on clear`() {
        val tracker = IncidentTracker<Net>()
        val network = Net(1)

        assertEquals(0L, tracker.currentId)

        val begun1 = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        assertEquals(begun1.id, tracker.currentId)

        val begun2 = tracker.begin(network, tunnelledInputs(), ProbePath.BOUND_WIFI, diagnostics())
        assertEquals(begun2.id, tracker.currentId)
        assertNotEquals(begun1.id, tracker.currentId)

        tracker.clear()
        assertEquals(0L, tracker.currentId)
    }

    private fun healthyDiagnosis() = DiagnosisResult(
        top = DiagnosticReport.Healthy,
        checks = emptyList(),
        recommended = RecommendedAction.NoActionAvailable,
    )
}
