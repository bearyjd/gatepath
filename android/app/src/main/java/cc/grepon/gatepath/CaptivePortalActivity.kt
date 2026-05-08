package cc.grepon.gatepath

import android.content.Intent
import android.net.CaptivePortal
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import cc.grepon.gatepath.ui.PortalScreen
import cc.grepon.gatepath.ui.theme.GatepathTheme
import dagger.hilt.android.AndroidEntryPoint
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
 *     routes via the captive interface. Receiving the CAPTIVE_PORTAL intent
 *     is the system's signal that this app is the chosen sign-in handler;
 *     the framework permits restricted-network access in this context,
 *     bypassing the `EPERM (Operation not permitted)` that direct probes
 *     hit when the network is captive.
 *
 *   - [ConnectivityManager.EXTRA_CAPTIVE_PORTAL_URL] — the URL the captive
 *     portal redirected to (the actual sign-in page). Available API 28+.
 *
 * On dismiss with success → [CaptivePortal.reportCaptivePortalDismissed].
 * On dismiss without success (back button, system kill) → [CaptivePortal.ignoreNetwork].
 */
@AndroidEntryPoint
class CaptivePortalActivity : ComponentActivity() {

    @Inject
    lateinit var connectivityManager: ConnectivityManager

    private var captivePortal: CaptivePortal? = null
    private var reported = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        captivePortal = readCaptivePortalExtra(intent)
        val portalUrl = intent.getStringExtra(ConnectivityManager.EXTRA_CAPTIVE_PORTAL_URL)
            ?: DEFAULT_CONNECTIVITY_CHECK_URL

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
        // routes via that interface. Receiving the CAPTIVE_PORTAL intent is
        // the system's signal that this app is the chosen sign-in handler;
        // the framework permits restricted-network access in this context,
        // bypassing the EPERM that direct probes hit.
        connectivityManager.bindProcessToNetwork(network)

        Log.i(
            TAG,
            "Handling captive portal for network $network at $portalUrl",
        )

        setContent {
            GatepathTheme {
                PortalScreen(
                    portalUrl = portalUrl,
                    network = network,
                    connectivityManager = connectivityManager,
                    onDismiss = ::reportSignedIn,
                    onBlockedNavigation = {},
                    onBlockedResource = {},
                )
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
        private const val DEFAULT_CONNECTIVITY_CHECK_URL =
            "http://connectivitycheck.gstatic.com/generate_204"
    }
}
