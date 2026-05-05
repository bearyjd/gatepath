package cc.grepon.gatepath.ui

import android.net.ConnectivityManager
import android.net.Network
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier

/**
 * Full-screen portal sheet.
 * Hosts [GatepathWebView] and exposes a dismiss button in the top bar.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PortalScreen(
    portalUrl: String,
    network: Network,
    connectivityManager: ConnectivityManager,
    onDismiss: () -> Unit,
    onBlockedNavigation: () -> Unit,
    onBlockedResource: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Scaffold(
        modifier = modifier,
        topBar = {
            TopAppBar(
                title = { Text("Network Sign-In", style = MaterialTheme.typography.titleMedium) },
                actions = {
                    Button(
                        onClick = onDismiss,
                        colors = ButtonDefaults.textButtonColors(),
                    ) {
                        Text("Dismiss")
                    }
                },
            )
        },
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
            contentAlignment = Alignment.TopStart,
        ) {
            GatepathWebView(
                url = portalUrl,
                network = network,
                connectivityManager = connectivityManager,
                onBlockedNavigation = onBlockedNavigation,
                onBlockedResource = onBlockedResource,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}
