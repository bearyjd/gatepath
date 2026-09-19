package com.ventouxlabs.gatepath

import android.content.Intent
import android.net.CaptivePortal
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.lifecycleScope
import com.ventouxlabs.gatepath.network.CONNECTIVITY_CHECK_URL
import com.ventouxlabs.gatepath.network.CaptivePortalMonitor
import com.ventouxlabs.gatepath.network.ClassificationInputs
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.PortalProbe
import com.ventouxlabs.gatepath.network.ProbeResult
import com.ventouxlabs.gatepath.network.VpnDetector
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.network.classify
import com.ventouxlabs.gatepath.ui.ConfinementAction
import com.ventouxlabs.gatepath.ui.ConfinementCard
import com.ventouxlabs.gatepath.ui.PortalScreen
import com.ventouxlabs.gatepath.ui.VpnAppLauncher
import com.ventouxlabs.gatepath.ui.theme.GatepathTheme
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.URI
import javax.inject.Inject

/**
 * System captive-portal handler.
 *
 * Launched by Android when the user taps the "Sign in to Wi-Fi network"
 * notification AND picks Gatepath in the chooser. The intent carries:
 *
 *   - [ConnectivityManager.EXTRA_CAPTIVE_PORTAL] — a [CaptivePortal] token
 *     used to report sign-in completion or dismissal back to the system.
 *
 *   - [ConnectivityManager.EXTRA_NETWORK] — the captive [Network]. The
 *     activity binds the process to this network via
 *     [ConnectivityManager.bindProcessToNetwork] so the WebView's traffic
 *     routes via the captive interface.
 *
 *   - [ConnectivityManager.EXTRA_CAPTIVE_PORTAL_URL] — the URL the captive
 *     portal redirected to (the actual sign-in page). Available API 28+.
 *
 * Arriving here is not proof that the traffic can reach the gateway: a
 * secure VPN still covers this UID, so the bind can still fail with EPERM.
 * The activity therefore classifies the network first ([ConfinementState])
 * and shows a WebView only from [ConfinementState.Confined]; every other
 * state gets the confinement card instead of a page that cannot load.
 *
 * On dismiss with success → [CaptivePortal.reportCaptivePortalDismissed].
 * On dismiss without success (back button, system kill) → [CaptivePortal.ignoreNetwork].
 */
@AndroidEntryPoint
class CaptivePortalActivity : ComponentActivity() {

    @Inject
    lateinit var connectivityManager: ConnectivityManager

    @Inject
    lateinit var probe: PortalProbe

    /**
     * Injected only for [CaptivePortalMonitor.probeUrl]. This entry point must
     * probe the same endpoint the monitor does — in debug builds `AppModule`
     * resolves that from `Settings.Global.captive_portal_http_url`, so a
     * hardcoded gstatic URL here would answer 204 over the emulator's real NAT
     * and classify a genuinely captive network as Unknown. See
     * `tests/e2e-android/HARNESS_NOTES.md` §2.
     */
    @Inject
    lateinit var monitor: CaptivePortalMonitor

    private var captivePortal: CaptivePortal? = null
    private var reported = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        captivePortal = readCaptivePortalExtra(intent)
        val portalUrl = intent.getStringExtra(ConnectivityManager.EXTRA_CAPTIVE_PORTAL_URL)
            ?: CONNECTIVITY_CHECK_URL

        val portal = captivePortal
        if (portal == null) {
            Log.w(TAG, "CAPTIVE_PORTAL intent missing CaptivePortal extra; finishing")
            finish()
            return
        }

        // The captive portal Network is delivered via EXTRA_NETWORK on newer
        // API levels. Fall back to the currently-bound or active network if
        // the extra is missing.
        val network: Network? = readNetworkExtra(intent)
            ?: connectivityManager.boundNetworkForProcess
            ?: connectivityManager.activeNetwork

        if (network == null) {
            Log.w(TAG, "No Network found for captive portal; finishing")
            portal.ignoreNetwork()
            finish()
            return
        }

        // Bind the process to the captive network so the WebView's traffic
        // routes via that interface.
        connectivityManager.bindProcessToNetwork(network)

        Log.i(
            TAG,
            "Handling captive portal for network $network at $portalUrl",
        )

        lifecycleScope.launch {
            val bound = withContext(Dispatchers.IO) { probe.probe(network, testUrl = monitor.probeUrl) }
            val vpn = withContext(Dispatchers.IO) { VpnDetector.detect() }
            val strict = connectivityManager.getLinkProperties(network)?.privateDnsServerName != null
            val resolved = (bound as? ProbeResult.Portal)?.let { p ->
                val host = runCatching { URI(p.locationUrl).host }.getOrNull()
                if (host == null || host.all { it.isDigit() || it == '.' } || host.contains(':')) null
                else withContext(Dispatchers.IO) { runCatching { network.getAllByName(host).isNotEmpty() }.getOrDefault(false) }
            }
            val state = classify(ClassificationInputs(bound, null, vpn.interfaces, strict, resolved))
            Log.i(TAG, "System handoff confinement: ${state.schemaName}")
            // The system delivered a URL; prefer it over the probe's when confined.
            val url = if (state is ConfinementState.Confined) portalUrl else null
            render(state, url, network)
        }
    }

    /**
     * Show the sign-in page when [url] is non-null, otherwise the one action
     * this confinement state allows. Sharing is absent on purpose: the
     * evidence bundle belongs to the ViewModel in `MainActivity`, and this
     * entry point has no session of its own to attach it to.
     */
    private fun render(state: ConfinementState, url: String?, network: Network) {
        setContent {
            GatepathTheme {
                if (url != null) {
                    PortalScreen(
                        portalUrl = url,
                        network = network,
                        connectivityManager = connectivityManager,
                        onDismiss = ::reportSignedIn,
                        onBlockedNavigation = {},
                        onBlockedResource = {},
                        onTlsCertErrorBypassed = {},
                        // No evidence record on this entry point — see render's KDoc.
                        onCertSummary = {},
                    )
                } else {
                    val kind = (state as? ConfinementState.Tunnelled)?.vpnKind
                        ?: (state as? ConfinementState.Blocked)?.vpnKind ?: VpnKind.NONE
                    val (label, launch) = VpnAppLauncher.resolve(this, kind)
                    ConfinementCard(
                        state = state,
                        vpnAppLabel = label,
                        onAction = { action ->
                            when (action) {
                                ConfinementAction.OPEN_VPN_APP -> startActivity(launch)
                                ConfinementAction.OPEN_NETWORK_SETTINGS ->
                                    startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
                                ConfinementAction.SIGN_IN_HERE, ConfinementAction.SHARE_EVIDENCE -> Unit
                            }
                        },
                        onShareEvidence = { /* no bundle on this entry; MainActivity owns sharing */ },
                    )
                }
            }
        }
    }

    /**
     * Tell the system the user signed in. The system re-validates the network
     * and clears the captive-portal flag. Subsequent connectivity goes
     * through the normal validated path.
     */
    private fun reportSignedIn() {
        if (reported) {
            finish()
            return
        }
        reported = true
        captivePortal?.reportCaptivePortalDismissed()
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        // Activity destroyed without reporting (back button, low-memory kill).
        // Tell the system we ignored the network so it falls back to its own
        // handler instead of waiting indefinitely for our reply.
        if (!reported) {
            captivePortal?.ignoreNetwork()
        }
    }

    @Suppress("DEPRECATION")
    private fun readCaptivePortalExtra(intent: Intent): CaptivePortal? {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(
                ConnectivityManager.EXTRA_CAPTIVE_PORTAL,
                CaptivePortal::class.java,
            )
        } else {
            intent.getParcelableExtra(ConnectivityManager.EXTRA_CAPTIVE_PORTAL)
        }
    }

    @Suppress("DEPRECATION")
    private fun readNetworkExtra(intent: Intent): Network? {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(
                ConnectivityManager.EXTRA_NETWORK,
                Network::class.java,
            )
        } else {
            intent.getParcelableExtra(ConnectivityManager.EXTRA_NETWORK)
        }
    }

    companion object {
        private const val TAG = "GatepathCaptive"
    }
}
