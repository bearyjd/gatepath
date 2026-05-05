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
private const val SESSION_TIMEOUT_MS = 10 * 60 * 1000L // 10 minutes

@HiltViewModel
class MainViewModel @Inject constructor(
    private val monitor: CaptivePortalMonitor,
    private val sessionManager: PortalSessionManager,
) : ViewModel() {

    private val _session = MutableStateFlow<PortalSession>(PortalSession.Idle)
    val session: StateFlow<PortalSession> = _session.asStateFlow()

    private val _activeNetwork = MutableStateFlow<Network?>(null)
    val activeNetwork: StateFlow<Network?> = _activeNetwork.asStateFlow()

    private var sessionOpenedUtc: String = ""

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
                            handleClose(CloseReason.ERROR, "Network lost")
                        }
                    }
                }
            }
        }
    }

    private fun openPortal() {
        _session.value = sessionManager.openPortal(_session.value)
        sessionOpenedUtc = utcNow()
        startSessionTimeout()
    }

    private fun startSessionTimeout() {
        viewModelScope.launch {
            delay(SESSION_TIMEOUT_MS)
            val current = _session.value
            if (current is PortalSession.Active) {
                Log.d(TAG, "Session timed out after 10 minutes")
                val next = sessionManager.timeout(current)
                _session.value = next
                writeAuditLog(next, CloseReason.TIMEOUT)
            }
        }
    }

    fun onDismiss() {
        val current = _session.value
        val next = sessionManager.dismiss(current)
        _session.value = next
        writeAuditLog(next, CloseReason.USER_DISMISSED)
    }

    fun onBlockedNavigation() {
        _session.value = sessionManager.recordBlockedNavigation(_session.value)
    }

    fun onBlockedResource() {
        _session.value = sessionManager.recordBlockedResource(_session.value)
    }

    private fun handleClose(reason: CloseReason, errorMsg: String = "") {
        val current = _session.value
        val next = if (reason == CloseReason.ERROR) {
            sessionManager.error(current, errorMsg)
        } else {
            sessionManager.dismiss(current)
        }
        _session.value = next
        writeAuditLog(next, reason)
    }

    private fun writeAuditLog(finalState: PortalSession, reason: CloseReason) {
        val (blockedNav, blockedRes) = when (finalState) {
            is PortalSession.Completed ->
                finalState.blockedNavigationAttempts to finalState.blockedResourceRequests
            is PortalSession.Active ->
                finalState.blockedNavigationAttempts to finalState.blockedResourceRequests
            else -> 0 to 0
        }

        val portalUrl = when (val s = _session.value) {
            is PortalSession.Active -> s.portalUrl
            is PortalSession.Detected -> s.portalUrl
            else -> ""
        }
        val closedUtc = utcNow()
        val openedInstant = runCatching { Instant.parse(sessionOpenedUtc) }.getOrElse { Instant.now() }
        val duration = (Instant.parse(closedUtc).epochSecond - openedInstant.epochSecond).toInt()

        val vpnInfo = VpnDetector.detect()
        val vpnIfaces = vpnInfo.interfaces.map { iface ->
            if (vpnInfo.isTailscaleFullTunnel && iface.startsWith("tailscale")) {
                iface.replace("split_tunnel", "full_tunnel")
            } else {
                iface
            }
        }

        val entry = AuditEntry(
            timestampUtc = closedUtc,
            ssid = null, // SSID retrieval requires ACCESS_FINE_LOCATION on Android 10+
            gatewayIp = null,
            portalDomain = runCatching { URI(portalUrl).host ?: portalUrl }.getOrDefault(portalUrl),
            vpnInterfacesDetected = vpnIfaces,
            vpnWarningShown = vpnIfaces.isNotEmpty(),
            sessionOpenedUtc = sessionOpenedUtc,
            sessionClosedUtc = closedUtc,
            closeReason = reason.schemaValue,
            durationSeconds = maxOf(0, duration),
            blockedNavigationAttempts = blockedNav,
            blockedResourceRequests = blockedRes,
        )

        viewModelScope.launch {
            AuditLog.append(entry)
        }
    }

    private fun utcNow(): String =
        DateTimeFormatter.ISO_INSTANT.format(Instant.now())
}
