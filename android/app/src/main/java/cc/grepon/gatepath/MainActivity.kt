package cc.grepon.gatepath

import android.net.ConnectivityManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.hilt.navigation.compose.hiltViewModel
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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContent {
            GatepathTheme {
                val viewModel: MainViewModel = hiltViewModel()
                val session by viewModel.session.collectAsState()
                val activeNetwork by viewModel.activeNetwork.collectAsState()
                val networkStatus by viewModel.networkStatus.collectAsState()
                val diagnostics by viewModel.latestDiagnostics.collectAsState()

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
                                onDismiss = viewModel::onDismiss,
                            )
                        }
                    }
                    else -> MainScreen(
                        session = s,
                        networkStatus = networkStatus,
                        diagnostics = diagnostics,
                        onDismiss = viewModel::onDismiss,
                    )
                }
            }
        }
    }
}
