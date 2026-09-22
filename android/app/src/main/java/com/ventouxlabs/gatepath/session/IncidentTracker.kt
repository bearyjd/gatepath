package com.ventouxlabs.gatepath.session

import android.net.Network
import com.ventouxlabs.gatepath.diag.DiagnosisResult
import com.ventouxlabs.gatepath.diag.IncidentEvidence
import com.ventouxlabs.gatepath.network.ClassificationInputs
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.NetworkDiagnostics
import com.ventouxlabs.gatepath.network.PortalProbeCapture
import com.ventouxlabs.gatepath.network.ProbePath
import com.ventouxlabs.gatepath.network.ProbeResult
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.network.classify
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.net.URI

/**
 * Single owner of everything scoped to one captive incident:
 * [confinement], [evidence], [diagnosis], [suspectedNetwork] and
 * [lastDiagnostics] — the five fields `MainViewModel` used to hold directly.
 *
 * [begin] classifies a new incident and hands back its id; every later write
 * ([updateEvidence], [adoptDefaultRouteCapture], [setDiagnosis],
 * [updateLastDiagnostics]) is keyed to that id and silently dropped once a
 * newer incident has begun — this is what stops a stale diagnostic-engine
 * run for incident 1 from writing into incident 2's published record.
 *
 * Pure Kotlin aside from [android.net.Network] (an opaque handle compared
 * only by reference/equality here), so this is exercised by the no-SDK JVM
 * suite.
 */
class IncidentTracker {

    private val _confinement = MutableStateFlow<ConfinementState?>(null)
    val confinement: StateFlow<ConfinementState?> = _confinement.asStateFlow()

    private val _evidence = MutableStateFlow<IncidentEvidence?>(null)
    val evidence: StateFlow<IncidentEvidence?> = _evidence.asStateFlow()

    private val _diagnosis = MutableStateFlow<DiagnosisResult?>(null)
    val diagnosis: StateFlow<DiagnosisResult?> = _diagnosis.asStateFlow()

    /** Network from the most recent incident — target for manual re-runs. */
    var suspectedNetwork: Network? = null
        private set

    /**
     * The monitor's environment snapshot for the current incident. Kept
     * separate from [evidence] because a rerun needs the full snapshot
     * (including `defaultRouteBypassesCaptive`) to rebuild an honest
     * `ProbeContext`, while [IncidentEvidence] deliberately carries only the
     * two probe errors.
     */
    var lastDiagnostics: NetworkDiagnostics? = null
        private set

    /**
     * The id of the incident currently published, or `0L` when none is live.
     * `0L` is never issued by [begin] (ids start at 1 and only increase), so
     * it doubles as a safe "nothing is current" sentinel: every keyed write
     * below compares against this and is a no-op once it no longer matches.
     * Exposed read-only so a caller that only holds a network (not the
     * [Begun] id from the original [begin] call, e.g. a manual rerun) can
     * still key a write to "whatever incident is live right now".
     */
    var currentId: Long = 0L
        private set
    private var nextId: Long = 1L

    /** What [begin] classified and published, plus the id later writes must key against. */
    data class Begun(val id: Long, val confinement: ConfinementState, val evidence: IncidentEvidence)

    /**
     * Classify a new incident on [network] and publish its [confinement] and
     * [evidence]. Clears any stale [diagnosis] rather than leaving a previous
     * incident's finding on screen. Builds the evidence record the same way
     * `MainViewModel.handleIncident` did, plus [IncidentEvidence.portalHost].
     */
    fun begin(
        network: Network,
        inputs: ClassificationInputs,
        boundPath: ProbePath,
        diagnostics: NetworkDiagnostics,
    ): Begun {
        val id = nextId++
        currentId = id
        val state = classify(inputs)
        val ev = IncidentEvidence(
            confinement = state.schemaName,
            probePath = boundPath,
            probeCapture = (inputs.bound as? ProbeResult.Portal)?.capture,
            resolverWifi = emptyList(),
            resolverDoh = emptyList(),
            certSummary = null,
            vpnKind = VpnKind.fromInterfaces(diagnostics.vpnInterfaces),
            vpnInterfaces = diagnostics.vpnInterfaces,
            privateDnsStrict = diagnostics.privateDnsServer != null,
            bindError = diagnostics.bindProbeError,
            fallbackError = diagnostics.fallbackProbeError,
            portalHost = portalHostFor(state),
        )
        suspectedNetwork = network
        lastDiagnostics = diagnostics
        _confinement.value = state
        _evidence.value = ev
        _diagnosis.value = null
        return Begun(id, state, ev)
    }

    /** Atomic update to the current incident's evidence; a no-op once [id] is stale. */
    fun updateEvidence(id: Long, transform: (IncidentEvidence) -> IncidentEvidence) {
        if (id != currentId) return
        _evidence.update { it?.let(transform) }
    }

    /**
     * Adopt a capture obtained from a probe that travelled the default route
     * (not the bound Wi-Fi) — only when the incident has no capture yet.
     * Overwriting an existing bound-Wi-Fi capture would contradict its own
     * [IncidentEvidence.probePath], so this also relabels the path to
     * [ProbePath.DEFAULT_ROUTE] rather than leaving it misdescribed as
     * [ProbePath.BOUND_WIFI].
     */
    fun adoptDefaultRouteCapture(id: Long, capture: PortalProbeCapture) {
        updateEvidence(id) { e ->
            if (e.probeCapture != null) e else e.copy(probeCapture = capture, probePath = ProbePath.DEFAULT_ROUTE)
        }
    }

    /** Publish a diagnostic-engine result; dropped once [id] is stale. */
    fun setDiagnosis(id: Long, result: DiagnosisResult) {
        if (id != currentId) return
        _diagnosis.value = result
    }

    /** Record a fresh environment snapshot from a manual rerun; dropped once [id] is stale. */
    fun updateLastDiagnostics(id: Long, diagnostics: NetworkDiagnostics) {
        if (id != currentId) return
        lastDiagnostics = diagnostics
    }

    /**
     * Clear the incident, but only when [network] is the one it's about.
     * Returns whether it cleared. Guards a caller that reacts to a network
     * event for a network other than the one the live incident suspects.
     */
    fun clearIf(network: Network): Boolean {
        if (network != suspectedNetwork) return false
        clear()
        return true
    }

    /** Unconditionally reset everything scoped to one captive incident. */
    fun clear() {
        currentId = 0L
        _confinement.value = null
        _evidence.value = null
        _diagnosis.value = null
        suspectedNetwork = null
        lastDiagnostics = null
    }

    /**
     * The captive portal's host, when [state] carries one without needing a
     * session: [ConfinementState.DnsStrict]'s own host, or the host of a
     * [ConfinementState.Confined] state's portal URL. `null` for every other
     * state, or when the URL has no parseable host.
     */
    private fun portalHostFor(state: ConfinementState): String? = when (state) {
        is ConfinementState.DnsStrict -> state.portalHost
        is ConfinementState.Confined -> runCatching { URI(state.portalUrl).host }.getOrNull()
        is ConfinementState.Tunnelled, is ConfinementState.Blocked, is ConfinementState.Unknown -> null
    }
}
