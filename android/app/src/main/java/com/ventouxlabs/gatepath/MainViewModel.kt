package com.ventouxlabs.gatepath

import android.net.Network
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ventouxlabs.gatepath.audit.AuditEntry
import com.ventouxlabs.gatepath.audit.AuditLog
import com.ventouxlabs.gatepath.diag.CertSummary
import com.ventouxlabs.gatepath.diag.DiagnosisResult
import com.ventouxlabs.gatepath.diag.DiagnosticEngine
import com.ventouxlabs.gatepath.diag.DiagnosticReport
import com.ventouxlabs.gatepath.diag.IncidentEvidence
import com.ventouxlabs.gatepath.diag.ProbeContext
import com.ventouxlabs.gatepath.network.CaptivePortalMonitor
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.HttpFetcher
import com.ventouxlabs.gatepath.network.NetworkDiagnostics
import com.ventouxlabs.gatepath.network.ProbeResult
import com.ventouxlabs.gatepath.network.NetworkEvent
import com.ventouxlabs.gatepath.network.PortalProbe
import com.ventouxlabs.gatepath.network.VpnDetector
import com.ventouxlabs.gatepath.session.CloseReason
import com.ventouxlabs.gatepath.session.IncidentTracker
import com.ventouxlabs.gatepath.session.PortalSession
import com.ventouxlabs.gatepath.session.PortalSessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.InetAddress
import java.net.URI
import java.time.Instant
import java.time.format.DateTimeFormatter
import javax.inject.Inject

private const val TAG = "GatepathVM"
private const val SESSION_TIMEOUT_MS = 10 * 60 * 1000L // 10 minutes — see SECURITY_MODEL.md

@HiltViewModel
class MainViewModel @Inject constructor(
    private val monitor: CaptivePortalMonitor,
    private val sessionManager: PortalSessionManager,
    private val diagnosticEngine: DiagnosticEngine,
) : ViewModel() {

    private val portalProbe = PortalProbe()
    private val httpFetcher = HttpFetcher()

    private val _session = MutableStateFlow<PortalSession>(PortalSession.Idle)
    val session: StateFlow<PortalSession> = _session.asStateFlow()

    private val _activeNetwork = MutableStateFlow<Network?>(null)
    val activeNetwork: StateFlow<Network?> = _activeNetwork.asStateFlow()

    /**
     * Latest classification of the current network. Surfaces the monitor's
     * observation to the UI so the user sees a real status — not a permanent
     * "Monitoring network…" with no feedback. Updated whenever the monitor
     * emits an event.
     */
    enum class NetworkStatus {
        /** No network observation yet. */
        Unknown,

        /** Validated WiFi with no captive portal. The common home/office case. */
        NoPortal,

        /** Captive portal detected; session is or is about to be Active. */
        CaptiveDetected,

        /** Sign-in succeeded; network became validated. */
        SignInComplete,

        /** Captive network was lost mid-session. */
        Lost,
    }

    private val _networkStatus = MutableStateFlow(NetworkStatus.Unknown)
    val networkStatus: StateFlow<NetworkStatus> = _networkStatus.asStateFlow()

    /**
     * Single owner of everything scoped to one captive incident
     * ([confinement], [evidence], [diagnosis], the suspected network, and the
     * monitor's environment snapshot). See [IncidentTracker] — id-versioned
     * writes are what stop a stale diagnostic-engine run for a previous
     * incident from writing into the one currently on screen.
     */
    private val incidents = IncidentTracker()

    /**
     * How the most recent captive incident classified. `null` means no
     * incident is live: the UI shows no confinement card at all rather than
     * guessing. In-app sign-in is offered only from
     * [ConfinementState.Confined] — see `docs/SECURITY_MODEL.md`.
     */
    val confinement: StateFlow<ConfinementState?> = incidents.confinement

    /**
     * The shareable record for the current incident. Produced in every
     * confinement state, not only when a session opens, and enriched in place
     * as the resolver answers and any certificate error arrive.
     */
    val evidence: StateFlow<IncidentEvidence?> = incidents.evidence

    /**
     * Debug-only hook the harness reads as a file; null in release. Set by
     * MainActivity.
     *
     * The monitor starts in [init], so an incident can classify before the
     * Activity exists. Setting a sink therefore replays the current state
     * immediately — otherwise that first classification would never reach the
     * artefact the harness polls.
     */
    @Volatile
    var debugStateSink: ((String) -> Unit)? = null
        set(sink) {
            field = sink
            val current = incidents.confinement.value
            if (sink != null && current != null) sink(current.schemaName)
        }

    /**
     * Result of the most recent diagnostic-engine run. Set when the monitor
     * emits [NetworkEvent.CaptiveIncident] and the engine has produced a
     * finding. UI consumes this to show the top finding + recommended action
     * under the confinement card.
     *
     * Cleared whenever the network transitions to a known-good state, so a
     * stale finding from a previous network can't linger.
     */
    val diagnosis: StateFlow<DiagnosisResult?> = incidents.diagnosis

    /**
     * Was the session that is currently open opened from a classified
     * [ConfinementState.Confined] state?
     *
     * Latched when the session opens, **not** read live at audit time. The
     * success path clears the tracker via `incidents.clearIf(event.network)`
     * before `handleSignInSuccess()` reaches `writeAuditLog`, and
     * `CaptiveNetworkLost` does the same — but that clear is conditional on
     * the event's network still being the one the tracker suspects (a later
     * incident on a different network may have moved it), so reading
     * `incidents.confinement.value` from the writer would not reliably
     * report `unconfined` for exactly the sessions that were confined. The
     * latch does not depend on whether that clear fired. Reset after each
     * write.
     */
    private var sessionWasConfined: Boolean = false

    /**
     * The [IncidentTracker] id of the incident that opened the session
     * currently running, or `0L` (the tracker's own "nothing is current"
     * sentinel) when no session is open. Latched alongside
     * [sessionWasConfined] wherever a reenter is Accepted, and reset
     * alongside it wherever a session ends.
     *
     * [onCertSummary] keys its evidence write to this rather than to
     * `incidents.currentId`: reading `currentId` there is a tautology
     * against [IncidentTracker]'s own staleness check — it would always
     * report "current", even for a cert error arriving from a WebView whose
     * session's incident has since been superseded by a new one on another
     * network. Latching the id at session-open time is what makes that
     * write droppable once the session it actually describes is no longer
     * live.
     */
    private var sessionIncidentId: Long = 0L

    /**
     * Handle to the in-flight session-timeout coroutine. Cancelled when the
     * user dismisses, the network drops, or a new session begins. Without this
     * cancellation the coroutine would survive a dismiss and fire 10 minutes
     * later against whatever Active session happened to be running then.
     */
    private var timeoutJob: Job? = null

    /**
     * Handle to the in-flight diagnostic-engine coroutine. Cancelled before
     * launching a new run — a new incident ([handleIncident]) or a manual
     * re-run ([rerunDiagnostics]) — so a slow probe from a superseded run
     * can't land its result after a newer one already published.
     * [IncidentTracker]'s id-versioned writes are a second, independent guard
     * against the same race; this cancellation additionally frees the
     * in-flight network calls instead of just discarding their result.
     */
    private var engineJob: Job? = null

    init {
        observeNetwork()
    }

    private fun observeNetwork() {
        viewModelScope.launch {
            _session.value = sessionManager.startMonitoring(_session.value)
            monitor.observe().collect { event ->
                when (event) {
                    is NetworkEvent.CaptiveIncident -> handleIncident(event)
                    is NetworkEvent.NetworkValidated -> {
                        // The portal sign-in succeeded — captive network now has
                        // NET_CAPABILITY_VALIDATED. Transition Active → Completed.
                        _networkStatus.value = NetworkStatus.SignInComplete
                        if (incidents.clearIf(event.network)) engineJob?.cancel()
                        if (_activeNetwork.value == event.network) {
                            handleSignInSuccess()
                        }
                    }
                    is NetworkEvent.NetworkObservedNoPortal -> {
                        // Validated WiFi observed for the first time. Tell the
                        // user "you're on a normal network, all good" instead
                        // of leaving them on "Monitoring network…" forever.
                        _networkStatus.value = NetworkStatus.NoPortal
                        if (incidents.clearIf(event.network)) engineJob?.cancel()
                    }
                    is NetworkEvent.CaptiveNetworkLost -> {
                        _networkStatus.value = NetworkStatus.Lost
                        if (incidents.clearIf(event.network)) engineJob?.cancel()
                        if (_activeNetwork.value == event.network) {
                            _activeNetwork.value = null
                            // Close only a session that is still open.
                            // _activeNetwork outlives the session it names —
                            // no close path nulls it — so it can still match
                            // after a dismiss or a completed sign-in. An
                            // unguarded close would then take an already
                            // Completed (or a Monitoring) session and run it
                            // through
                            // PortalSessionManager.error — writing an audit
                            // entry with an empty portal_domain for a session
                            // that never was, and leaving the state machine
                            // Completed, where portalDetected no longer applies
                            // and automatic sign-in is dead for the rest of the
                            // process.
                            val current = _session.value
                            if (current is PortalSession.Active || current is PortalSession.Detected) {
                                // The manager picks ABORTED_PRE_ACTIVE for
                                // Detected and ERROR for Active; we always pass
                                // ERROR and let it decide based on phase.
                                handleClose(CloseReason.ERROR, "Network lost")
                            }
                        }
                    }
                }
            }
        }
    }

    /**
     * Classify one captive incident, publish the evidence, and open the
     * sign-in session only when the traffic is actually [ConfinementState.Confined]
     * to this Wi-Fi. Every other state leaves the session closed and lets the
     * confinement card tell the user what to do instead.
     *
     * [NetworkEvent.CaptiveIncident] deliberately does not clear the tracker
     * first: [IncidentTracker.begin] overwrites every incident-scoped field,
     * and clearing first would publish a transient null to collectors.
     */
    private fun handleIncident(event: NetworkEvent.CaptiveIncident) {
        val begun = incidents.begin(event.network, event.inputs, event.boundPath, event.diagnostics)
        val state = begun.confinement
        _networkStatus.value = NetworkStatus.CaptiveDetected
        debugStateSink?.invoke(state.schemaName)
        Log.i(TAG, "Confinement on ${event.network}: ${state.schemaName}")
        if (state is ConfinementState.Confined) {
            // reenter, not portalDetected: after a dismiss or a lost network
            // the session is Completed, which portalDetected rejects, so a
            // second captive network in the same process would never open.
            val result = sessionManager.reenter(_session.value, state.portalUrl)
            // Latch, retarget and open only when `result` is Accepted. Branching
            // on the ReenterResult type — not on the returned session's type —
            // is what makes this safe: a rejected reenter from a session that
            // is already Detected returns that same Detected session, which
            // `is PortalSession.Detected` could not tell apart from a real
            // transition. An Accepted reenter leaves the session untouched and
            // opens nothing, so claiming the session was confined would attach
            // this incident's verdict to whatever session is really running.
            //
            // `_activeNetwork` is the network of the open (or opening) session
            // — never the latest incident. `reenter` rejects from Active, so a
            // second Confined network arriving mid-sign-in must leave it alone:
            // retargeting it there re-keys MainActivity's PortalScreen (wiping
            // the portal's cookies mid-flow) and breaks the
            // `_activeNetwork == event.network` tests on NetworkValidated and
            // CaptiveNetworkLost, losing the portal_completed audit entry.
            // `incidents.suspectedNetwork` above keeps the "latest incident"
            // meaning for rerunDiagnostics; `signInHere` reads that field itself.
            when (result) {
                is PortalSessionManager.ReenterResult.Accepted -> {
                    _session.value = result.session
                    sessionWasConfined = true
                    sessionIncidentId = begun.id
                    _activeNetwork.value = event.network
                    openPortal()
                }
                is PortalSessionManager.ReenterResult.Rejected -> {
                    _session.value = result.current
                }
            }
        }
        runDiagnosticEngine(begun.id, event.network, event.diagnostics)
    }

    /**
     * Run the [DiagnosticEngine] against the suspected captive [network] and
     * publish the result to [diagnosis]. Builds a [ProbeContext] from the
     * monitor's [NetworkDiagnostics] snapshot — most fields are already
     * collected there, the only addition is the active-probe callable.
     *
     * The active-probe closure deliberately invokes `portalProbe.probe(null)`
     * (no bind) — the monitor has just run the bound probe for this incident,
     * so repeating it would only restate what the incident's classification
     * inputs already carry. The default-route probe instead exercises whether the userspace
     * fallback is working (e.g. the VPN was just paused). It probes
     * `monitor.probeUrl` — the same URL the monitor itself uses, which in
     * debug builds may be overridden to point at a mock portal — rather than
     * a hardcoded endpoint, so the diagnostic battery agrees with the monitor
     * about what "captive" means.
     *
     * Cancels any previously running engine job before launching this one —
     * a new incident or a manual rerun both call this, and a slow probe from
     * a superseded run must not land after a newer one already published.
     * Every write below is keyed to [id] through [IncidentTracker], which is
     * a second, independent guard against the same race.
     */
    private fun runDiagnosticEngine(id: Long, network: Network, diagnostics: NetworkDiagnostics) {
        engineJob?.cancel()
        engineJob = viewModelScope.launch {
            val ctx = ProbeContext(
                networkId = diagnostics.networkId,
                isPrivateDnsActive = diagnostics.privateDnsActive,
                privateDnsServer = diagnostics.privateDnsServer,
                httpProxyDescription = diagnostics.httpProxyDescription,
                vpnInterfaces = diagnostics.vpnInterfaces,
                isTailscaleFullTunnel = diagnostics.isTailscaleFullTunnel,
                dnsServerCount = diagnostics.dnsServerCount,
                hasValidatedCellular = diagnostics.hasValidatedCellular,
                defaultRouteBypassesCaptive = diagnostics.defaultRouteBypassesCaptive,
                probeUrl = monitor.probeUrl,
                httpFetch = { url, accept -> httpFetcher.fetch(network = null, url = url, accept = accept) },
                // When we are genuinely confined to this Wi-Fi, resolve
                // through THAT network's resolver — the system resolver would
                // answer from whatever path currently owns the default route
                // and the answer would describe the wrong network.
                resolveHost = { host ->
                    // Both resolvers block. The engine invokes this from the
                    // viewModelScope coroutine, i.e. Dispatchers.Main.immediate,
                    // where a blocking lookup throws NetworkOnMainThreadException
                    // and every answer comes back empty.
                    withContext(Dispatchers.IO) {
                        runCatching {
                            val addrs = if (incidents.confinement.value is ConfinementState.Confined) {
                                network.getAllByName(host)
                            } else {
                                InetAddress.getAllByName(host)
                            }
                            addrs.mapNotNull { it.hostAddress }
                        }.getOrElse { emptyList() }
                    }.also { answers ->
                        incidents.updateEvidence(id) { it.copy(resolverWifi = answers) }
                    }
                },
                // Keep whatever this probe intercepts, but only when the
                // incident has no capture yet: this one travelled the default
                // route, so letting it overwrite a bound-Wi-Fi capture would
                // contradict the record's own `probePath`.
                //
                // This is also what ProbeContext.defaultRouteBypassesCaptiveResolved()
                // calls when `defaultRouteBypassesCaptive` above is null (no
                // fallback probe ran for this incident) — it is the only
                // source of a fresh default-route measurement here, so the
                // tri-state's lazy resolution and this probe's own default-
                // route check are deliberately the same call.
                activeProbe = {
                    portalProbe.probe(network = null, testUrl = monitor.probeUrl).also { result ->
                        if (result is ProbeResult.Portal) {
                            result.capture?.let { fresh -> incidents.adoptDefaultRouteCapture(id, fresh) }
                        }
                    }
                },
            )
            val result = diagnosticEngine.run(ctx)
            // The DNS-hijack probe is the only component that asks a resolver
            // Gatepath does not control, so it is the only source for the
            // second half of the resolver comparison in the evidence record.
            result.checks
                .firstNotNullOfOrNull { it.report as? DiagnosticReport.DnsHijack }
                ?.let { hijack ->
                    incidents.updateEvidence(id) { it.copy(resolverDoh = listOf(hijack.doHAnswer)) }
                }
            Log.i(TAG, "Diagnosis on ${network}: top=${result.top::class.simpleName} action=${result.recommended}")
            incidents.setDiagnosis(id, result)
        }
    }

    /**
     * Manual re-run from the UI ("Run diagnostics again"). Re-snapshots the
     * environment for the suspected network — so a just-paused VPN or a fixed
     * proxy shows up — and re-runs the engine. The original probe errors, and
     * the tri-state `defaultRouteBypassesCaptive`, are carried over as-is
     * (never defaulted to `false`) — the engine's HttpProbe independently
     * re-tests the network path. No-op if nothing is suspected or the
     * snapshot fails (network torn down mid-tap): the previous diagnosis
     * stays on screen rather than flashing to empty.
     */
    fun rerunDiagnostics() {
        val network = incidents.suspectedNetwork ?: return
        val previous = incidents.lastDiagnostics
        val fresh = runCatching {
            monitor.snapshotDiagnostics(
                network = network,
                bindError = previous?.bindProbeError,
                fallbackError = previous?.fallbackProbeError,
                defaultRouteBypassesCaptive = previous?.defaultRouteBypassesCaptive,
            )
        }.getOrElse { ex ->
            Log.w(TAG, "Diagnostics re-run snapshot failed: ${ex.message}")
            return
        }
        val id = incidents.currentId
        incidents.updateLastDiagnostics(id, fresh)
        runDiagnosticEngine(id, network, fresh)
    }

    /**
     * The WebView saw a certificate error; fold its safe summary into the
     * evidence of the incident that opened the session it came from.
     *
     * Keyed to [sessionIncidentId], not `incidents.currentId`: the latter is
     * a tautology against [IncidentTracker]'s own staleness check — it is
     * always "current" — so it would still attach a stale WebView's cert
     * error to whatever incident happens to be live now, even after that
     * WebView's own session and incident have been superseded (e.g. a
     * second captive network arrived and classified while the first
     * session's WebView was still open in the background). Keying to the
     * id latched at session-open time is what makes this write droppable
     * once the session it actually describes is no longer live.
     */
    fun onCertSummary(summary: CertSummary) {
        incidents.updateEvidence(sessionIncidentId) { it.copy(certSummary = summary) }
    }

    /**
     * User pressed "Sign in here". Only meaningful from Confined; otherwise a
     * no-op.
     *
     * Uses [PortalSessionManager.reenter] rather than `portalDetected`: this
     * button is reachable after a dismiss, which leaves the session
     * `Completed`, and `portalDetected` accepts only `Monitoring`.
     *
     * Reads the network from [IncidentTracker.suspectedNetwork] rather than
     * relying on [handleIncident] to have set `_activeNetwork`: the incident
     * that raised this card may have had its `reenter` rejected, in which
     * case it deliberately left `_activeNetwork` pointing at the session that
     * was running then. The tracker's suspected network is cleared alongside
     * `confinement` in `IncidentTracker.clear`, so a non-null `Confined`
     * state implies a non-null network here; the elvis is a guard, not a
     * fallback.
     */
    fun signInHere() {
        val network = incidents.suspectedNetwork ?: return
        val state = incidents.confinement.value as? ConfinementState.Confined ?: return
        if (_session.value is PortalSession.Active) return
        val result = sessionManager.reenter(_session.value, state.portalUrl)
        // Same ordering as handleIncident: branch on ReenterResult, not on the
        // returned session's type, and latch/retarget/open only on Accepted.
        when (result) {
            is PortalSessionManager.ReenterResult.Accepted -> {
                _session.value = result.session
                sessionWasConfined = true
                sessionIncidentId = incidents.currentId
                _activeNetwork.value = network
                openPortal()
            }
            is PortalSessionManager.ReenterResult.Rejected -> {
                _session.value = result.current
            }
        }
    }

    private fun openPortal() {
        // Cancel any prior timeout — defensively, in case a previous session
        // was abandoned without going through handleClose.
        timeoutJob?.cancel()
        _session.value = sessionManager.openPortal(_session.value, utcNow())
        startSessionTimeout()
    }

    private fun startSessionTimeout() {
        timeoutJob = viewModelScope.launch {
            delay(SESSION_TIMEOUT_MS)
            val current = _session.value
            if (current is PortalSession.Active) {
                Log.d(TAG, "Session timed out after 10 minutes")
                val next = sessionManager.timeout(current, utcNow())
                _session.value = next
                writeAuditLog(next)
                sessionWasConfined = false
                sessionIncidentId = 0L
            }
        }
    }

    fun onDismiss() {
        timeoutJob?.cancel()
        timeoutJob = null
        val current = _session.value
        val next = sessionManager.dismiss(current, utcNow())
        _session.value = next
        writeAuditLog(next)
        sessionWasConfined = false
        sessionIncidentId = 0L
        // The confinement card deliberately stays on screen: the user can press
        // "Sign in here" again, which is what PortalSessionManager.reenter is for.
    }

    /**
     * The captive network became validated — the user signed in successfully.
     * Transition Active → Completed(PORTAL_COMPLETED) and write the audit entry.
     * If the session was never Active, no audit entry is written.
     */
    private fun handleSignInSuccess() {
        timeoutJob?.cancel()
        timeoutJob = null
        val current = _session.value
        if (current !is PortalSession.Active) {
            Log.d(TAG, "NetworkValidated received but session not Active (was $current)")
            return
        }
        val next = sessionManager.completePortal(current, utcNow())
        _session.value = next
        writeAuditLog(next)
        sessionWasConfined = false
        sessionIncidentId = 0L
    }

    fun onBlockedNavigation() {
        _session.value = sessionManager.recordBlockedNavigation(_session.value)
    }

    fun onBlockedResource() {
        _session.value = sessionManager.recordBlockedResource(_session.value)
    }

    fun onTlsCertErrorBypassed() {
        _session.value = sessionManager.recordTlsCertErrorBypassed(_session.value)
    }

    /**
     * Debug-only: jump straight to PortalSession.Active with [portalUrl] and
     * [network], bypassing the captive-portal detection pipeline. Lets the
     * PortalScreen/WebView code path be exercised on devices whose system
     * captive detection is unreachable (e.g. GrapheneOS hardcoded probe URLs).
     *
     * Skips the session manager and audit log on purpose — Dismiss returns to
     * Idle without persisting anything. Callers must gate on BuildConfig.DEBUG.
     */
    fun debugForceActiveSession(portalUrl: String, network: Network) {
        // This path never classifies, so the session is not confined — and the
        // latch could still be set from an earlier real session.
        sessionWasConfined = false
        sessionIncidentId = 0L
        _activeNetwork.value = network
        _session.value = PortalSession.Active(
            portalUrl = portalUrl,
            openedUtc = utcNow(),
        )
    }

    /**
     * Closes the session with [requestedReason]. The manager may downgrade ERROR
     * to ABORTED_PRE_ACTIVE for pre-Active phases, so the actual close reason
     * comes from the resulting [PortalSession.Completed].
     */
    private fun handleClose(requestedReason: CloseReason, errorMsg: String = "") {
        timeoutJob?.cancel()
        timeoutJob = null
        val current = _session.value
        val next = if (requestedReason == CloseReason.ERROR) {
            sessionManager.error(current, utcNow(), errorMsg)
        } else {
            sessionManager.dismiss(current, utcNow())
        }
        _session.value = next
        writeAuditLog(next)
        sessionWasConfined = false
        sessionIncidentId = 0L
    }

    /**
     * Writes a single audit entry derived entirely from [finalState]. This is
     * a pure function of the state — no var reads, no time recomputation.
     *
     * Skips the write when [finalState] is not Completed:
     * - Idle/Monitoring: there was never a session worth logging.
     * - Error: an Idle→Error path (rare, used only for unrecoverable startup
     *   errors with no live session). Active errors are mapped to
     *   Completed(ERROR) by the manager and DO produce an audit entry.
     */
    private fun writeAuditLog(finalState: PortalSession) {
        if (finalState !is PortalSession.Completed) {
            return
        }
        // Manager-produced timestamps are always valid ISO-8601 (utcNow uses
        // DateTimeFormatter.ISO_INSTANT). The defensive parse is kept as a
        // single-line guard against future manager changes — if that ever
        // returns 0, the next test failure will reveal it.
        val durationSeconds = runCatching {
            val opened = Instant.parse(finalState.openedUtc).epochSecond
            val closed = Instant.parse(finalState.closedUtc).epochSecond
            (closed - opened).coerceAtLeast(0).toInt()
        }.getOrDefault(0)

        val vpnInfo = VpnDetector.detect()
        val vpnIfaces = vpnInfo.interfaces.map { iface ->
            if (vpnInfo.isTailscaleFullTunnel && iface.startsWith("tailscale")) {
                iface.replace("split_tunnel", "full_tunnel")
            } else {
                iface
            }
        }

        val portalDomain = runCatching { URI(finalState.portalUrl).host ?: finalState.portalUrl }
            .getOrDefault(finalState.portalUrl)

        val entry = AuditEntry(
            timestampUtc = finalState.closedUtc,
            ssid = null, // SSID retrieval requires ACCESS_FINE_LOCATION on Android 10+
            gatewayIp = null,
            portalDomain = portalDomain,
            vpnInterfacesDetected = vpnIfaces,
            vpnWarningShown = vpnIfaces.isNotEmpty(),
            sessionOpenedUtc = finalState.openedUtc,
            sessionClosedUtc = finalState.closedUtc,
            closeReason = finalState.closeReason.schemaValue,
            durationSeconds = durationSeconds,
            observedNavigationAttempts = finalState.blockedNavigationAttempts,
            observedResourceRequests = finalState.blockedResourceRequests,
            // Latched at session open, not read live: the tracker's clear on
            // a validated or lost network is conditional on that network
            // still being the suspected one, so reading
            // incidents.confinement.value here would not reliably report
            // "unconfined" for precisely the confined sessions.
            confinement = if (sessionWasConfined) "confined" else "unconfined",
            tlsCertErrorsBypassed = finalState.tlsCertErrorsBypassed,
        )

        viewModelScope.launch {
            AuditLog.append(entry)
        }
    }

    private fun utcNow(): String =
        DateTimeFormatter.ISO_INSTANT.format(Instant.now())
}
