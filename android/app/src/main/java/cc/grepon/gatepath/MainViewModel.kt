package cc.grepon.gatepath

import android.net.Network
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.grepon.gatepath.audit.AuditEntry
import cc.grepon.gatepath.audit.AuditLog
import cc.grepon.gatepath.network.CaptivePortalMonitor
import cc.grepon.gatepath.network.NetworkEvent
import cc.grepon.gatepath.network.VpnDetector
import cc.grepon.gatepath.session.CloseReason
import cc.grepon.gatepath.session.PortalSession
import cc.grepon.gatepath.session.PortalSessionManager
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
) : ViewModel() {

    private val _session = MutableStateFlow<PortalSession>(PortalSession.Idle)
    val session: StateFlow<PortalSession> = _session.asStateFlow()

    private val _activeNetwork = MutableStateFlow<Network?>(null)
    val activeNetwork: StateFlow<Network?> = _activeNetwork.asStateFlow()

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
                        _session.value = sessionManager.portalDetected(_session.value, event.portalUrl)
                        openPortal()
                    }
                    is NetworkEvent.CaptiveNetworkLost -> {
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

    fun onBlockedNavigation() {
        _session.value = sessionManager.recordBlockedNavigation(_session.value)
    }

    fun onBlockedResource() {
        _session.value = sessionManager.recordBlockedResource(_session.value)
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
