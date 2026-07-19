package com.ventouxlabs.gatepath

import android.net.Network
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ventouxlabs.gatepath.audit.AuditEntry
import com.ventouxlabs.gatepath.audit.AuditLog
import com.ventouxlabs.gatepath.diag.DiagnosisResult
import com.ventouxlabs.gatepath.diag.DiagnosticEngine
import com.ventouxlabs.gatepath.diag.ProbeContext
import com.ventouxlabs.gatepath.network.CaptivePortalMonitor
import com.ventouxlabs.gatepath.network.NetworkDiagnostics
import com.ventouxlabs.gatepath.network.NetworkEvent
import com.ventouxlabs.gatepath.network.PortalProbe
import com.ventouxlabs.gatepath.network.VpnDetector
import com.ventouxlabs.gatepath.session.CloseReason
import com.ventouxlabs.gatepath.session.PortalSession
import com.ventouxlabs.gatepath.session.PortalSessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
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

        /**
         * Network looks captive but our probe was refused (typically EPERM
         * because Android marks captive networks as restricted). The user
         * needs to tap the system Wi-Fi "Sign in" notification and pick
         * Gatepath as the handler — that path delivers a CaptivePortal token
         * which bypasses the restriction.
         */
        CaptivePending,
    }

    private val _networkStatus = MutableStateFlow(NetworkStatus.Unknown)
    val networkStatus: StateFlow<NetworkStatus> = _networkStatus.asStateFlow()

    /**
     * Diagnostics from the most recent failed-probe attempt. Surfaces in the
     * troubleshooting panel when [networkStatus] is [NetworkStatus.CaptivePending].
     * Cleared whenever the network transitions to a known-good state
     * (validated / sign-in complete / captive confirmed) so stale info doesn't
     * persist across networks.
     */
    private val _latestDiagnostics = MutableStateFlow<NetworkDiagnostics?>(null)
    val latestDiagnostics: StateFlow<NetworkDiagnostics?> = _latestDiagnostics.asStateFlow()

    /**
     * Result of the most recent diagnostic-engine run. Set when the monitor
     * emits [NetworkEvent.CaptivePortalSuspected] and the engine has produced
     * a finding. UI consumes this to show the top finding + recommended action
     * above the existing static troubleshooting list.
     *
     * Cleared whenever the network transitions to a known-good state, so a
     * stale finding from a previous network can't linger.
     */
    private val _diagnosis = MutableStateFlow<DiagnosisResult?>(null)
    val diagnosis: StateFlow<DiagnosisResult?> = _diagnosis.asStateFlow()

    /** Network from the most recent CaptivePortalSuspected — target for manual re-runs. */
    private var suspectedNetwork: Network? = null

    /**
     * Handle to the in-flight session-timeout coroutine. Cancelled when the
     * user dismisses, the network drops, or a new session begins. Without this
     * cancellation the coroutine would survive a dismiss and fire 10 minutes
     * later against whatever Active session happened to be running then.
     */
    private var timeoutJob: Job? = null

    init {
        observeNetwork()
    }

    private fun observeNetwork() {
        viewModelScope.launch {
            _session.value = sessionManager.startMonitoring(_session.value)
            monitor.observe().collect { event ->
                when (event) {
                    is NetworkEvent.CaptiveNetworkAvailable -> {
                        _activeNetwork.value = event.network
                        _networkStatus.value = NetworkStatus.CaptiveDetected
                        _latestDiagnostics.value = null
                        _diagnosis.value = null
                        suspectedNetwork = null
                        _session.value = sessionManager.portalDetected(_session.value, event.portalUrl)
                        openPortal()
                    }
                    is NetworkEvent.NetworkValidated -> {
                        // The portal sign-in succeeded — captive network now has
                        // NET_CAPABILITY_VALIDATED. Transition Active → Completed.
                        _networkStatus.value = NetworkStatus.SignInComplete
                        _latestDiagnostics.value = null
                        _diagnosis.value = null
                        suspectedNetwork = null
                        if (_activeNetwork.value == event.network) {
                            handleSignInSuccess()
                        }
                    }
                    is NetworkEvent.NetworkObservedNoPortal -> {
                        // Validated WiFi observed for the first time. Tell the
                        // user "you're on a normal network, all good" instead
                        // of leaving them on "Monitoring network…" forever.
                        _networkStatus.value = NetworkStatus.NoPortal
                        _latestDiagnostics.value = null
                        _diagnosis.value = null
                        suspectedNetwork = null
                    }
                    is NetworkEvent.CaptivePortalSuspected -> {
                        // Both probe paths failed. Diagnostics carries VPN
                        // status, Private DNS, proxy, and raw probe errors so
                        // the UI can guide the user through the troubleshooting
                        // pathway.
                        Log.w(TAG, "Captive suspected on ${event.network}: ${event.diagnostics}")
                        _latestDiagnostics.value = event.diagnostics
                        _networkStatus.value = NetworkStatus.CaptivePending
                        suspectedNetwork = event.network
                        runDiagnosticEngine(event.network, event.diagnostics)
                    }
                    is NetworkEvent.CaptiveNetworkLost -> {
                        _networkStatus.value = NetworkStatus.Lost
                        _latestDiagnostics.value = null
                        _diagnosis.value = null
                        suspectedNetwork = null
                        if (_activeNetwork.value == event.network) {
                            _activeNetwork.value = null
                            // The manager picks ABORTED_PRE_ACTIVE for pre-Active
                            // states and ERROR for Active; we always pass ERROR
                            // and let the manager decide based on phase.
                            handleClose(CloseReason.ERROR, "Network lost")
                        }
                    }
                }
            }
        }
    }

    /**
     * Run the [DiagnosticEngine] against the suspected captive [network] and
     * publish the result to [diagnosis]. Builds a [ProbeContext] from the
     * monitor's [NetworkDiagnostics] snapshot — most fields are already
     * collected there, the only addition is the active-probe callable.
     *
     * The active-probe closure deliberately invokes `portalProbe.probe(null)`
     * (no bind) — bind has already failed by the time we reach this branch
     * (that's what triggered Suspected), so re-running it would just confirm
     * EPERM. The default-route probe instead exercises whether the userspace
     * fallback might be working now (e.g. VPN was just paused).
     */
    private fun runDiagnosticEngine(network: Network, diagnostics: NetworkDiagnostics) {
        viewModelScope.launch {
            val ctx = ProbeContext(
                networkId = diagnostics.networkId,
                isPrivateDnsActive = diagnostics.privateDnsActive,
                privateDnsServer = diagnostics.privateDnsServer,
                httpProxyDescription = diagnostics.httpProxyDescription,
                vpnInterfaces = diagnostics.vpnInterfaces,
                isTailscaleFullTunnel = diagnostics.isTailscaleFullTunnel,
                dnsServerCount = diagnostics.dnsServerCount,
                hasValidatedCellular = diagnostics.hasValidatedCellular,
                activeProbe = { portalProbe.probe(network = null) },
            )
            val result = diagnosticEngine.run(ctx)
            Log.i(TAG, "Diagnosis on ${network}: top=${result.top::class.simpleName} action=${result.recommended}")
            _diagnosis.value = result
        }
    }

    /**
     * Manual re-run from the UI ("Run diagnostics again"). Re-snapshots the
     * environment for the suspected network — so a just-paused VPN or a fixed
     * proxy shows up — and re-runs the engine. The original probe errors are
     * carried over; the engine's HttpProbe independently re-tests the network
     * path. No-op if nothing is suspected or the snapshot fails (network torn
     * down mid-tap): the previous diagnosis stays on screen rather than
     * flashing to empty.
     */
    fun rerunDiagnostics() {
        val network = suspectedNetwork ?: return
        val previous = _latestDiagnostics.value
        val fresh = runCatching {
            monitor.snapshotDiagnostics(
                network = network,
                bindError = previous?.bindProbeError,
                fallbackError = previous?.fallbackProbeError,
            )
        }.getOrElse { ex ->
            Log.w(TAG, "Diagnostics re-run snapshot failed: ${ex.message}")
            return
        }
        _latestDiagnostics.value = fresh
        runDiagnosticEngine(network, fresh)
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
    }

    fun onBlockedNavigation() {
        _session.value = sessionManager.recordBlockedNavigation(_session.value)
    }

    fun onBlockedResource() {
        _session.value = sessionManager.recordBlockedResource(_session.value)
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
            blockedNavigationAttempts = finalState.blockedNavigationAttempts,
            blockedResourceRequests = finalState.blockedResourceRequests,
        )

        viewModelScope.launch {
            AuditLog.append(entry)
        }
    }

    private fun utcNow(): String =
        DateTimeFormatter.ISO_INSTANT.format(Instant.now())
}
