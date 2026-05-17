package cc.grepon.gatepath

import android.content.Intent
import android.net.ConnectivityManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import cc.grepon.gatepath.session.PortalSession
import cc.grepon.gatepath.ui.MainScreen
import cc.grepon.gatepath.ui.PortalScreen
import cc.grepon.gatepath.ui.theme.GatepathTheme
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject
    lateinit var connectivityManager: ConnectivityManager

    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        maybeApplyDebugIntent(intent)

        setContent {
            GatepathTheme {
                val session by viewModel.session.collectAsState()
                val activeNetwork by viewModel.activeNetwork.collectAsState()
                val networkStatus by viewModel.networkStatus.collectAsState()
                val diagnostics by viewModel.latestDiagnostics.collectAsState()
                val diagnosis by viewModel.diagnosis.collectAsState()

                when (val s = session) {
                    is PortalSession.Active -> {
                        val network = activeNetwork
                        if (network != null) {
                            PortalScreen(
                                portalUrl = s.portalUrl,
                                network = network,
                                connectivityManager = connectivityManager,
                                onDismiss = viewModel::onDismiss,
                                onBlockedNavigation = viewModel::onBlockedNavigation,
                                onBlockedResource = viewModel::onBlockedResource,
                            )
                        } else {
                            MainScreen(
                                session = s,
                                networkStatus = networkStatus,
                                diagnostics = diagnostics,
                                diagnosis = diagnosis,
                                onDismiss = viewModel::onDismiss,
                            )
                        }
                    }
                    else -> MainScreen(
                        session = s,
                        networkStatus = networkStatus,
                        diagnostics = diagnostics,
                        diagnosis = diagnosis,
                        onDismiss = viewModel::onDismiss,
                    )
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        maybeApplyDebugIntent(intent)
    }

    /**
     * Debug-only entry point: open PortalScreen against a user-supplied URL
     * without going through the captive-portal detection pipeline. Exists for
     * smoke-testing the WebView/PortalScreen code path on devices whose system
     * captive detection is unreachable (e.g. GrapheneOS hardcodes the probe
     * URLs in its NetworkStack module, ignoring Settings.Global overrides).
     *
     * Fire from adb:
     *   adb shell am start -n cc.grepon.gatepath/.MainActivity \
     *       --es gatepath.debug.portal_url "http://your-portal/portal"
     */
    private fun maybeApplyDebugIntent(intent: Intent) {
        if (!BuildConfig.DEBUG) return
        val url = intent.getStringExtra(EXTRA_DEBUG_PORTAL_URL) ?: return
        val net = connectivityManager.activeNetwork ?: run {
            Log.w(TAG, "Debug portal intent: no active network; ignored")
            return
        }
        Log.i(TAG, "Debug portal intent: opening $url on $net")
        viewModel.debugForceActiveSession(url, net)
    }

    companion object {
        private const val TAG = "GatepathMain"
        private const val EXTRA_DEBUG_PORTAL_URL = "gatepath.debug.portal_url"
    }
}
