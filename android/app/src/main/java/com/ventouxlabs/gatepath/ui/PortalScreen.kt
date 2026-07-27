package com.ventouxlabs.gatepath.ui

import android.net.ConnectivityManager
import android.net.Network
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.ventouxlabs.gatepath.BuildConfig

/**
 * Full-screen portal sheet.
 * Hosts [GatepathWebView] and exposes a dismiss button in the top bar.
 *
 * Owns the load-error state: when the WebView fails a main-frame load, this
 * screen covers the (blank) page with [PortalLoadErrorPanel] rather than
 * leaving the user staring at white. See [PortalLoadError].
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
    onTlsCertErrorBypassed: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var loadError by remember { mutableStateOf<PortalLoadError?>(null) }
    var reloadToken by remember { mutableStateOf(0) }

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
                onTlsCertErrorBypassed = onTlsCertErrorBypassed,
                onLoadStarted = { loadError = null },
                onLoadError = { loadError = it },
                reloadToken = reloadToken,
                modifier = Modifier.fillMaxSize(),
            )

            // Drawn over the WebView, opaque: the failed load leaves a blank
            // document behind and showing it through would defeat the point.
            loadError?.let { error ->
                PortalLoadErrorPanel(
                    error = error,
                    onRetry = {
                        loadError = null
                        reloadToken += 1
                    },
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }
    }
}

/**
 * The "it didn't load, here's why" panel.
 *
 * Copy comes from [PortalLoadErrorText], which is unit-tested; this composable
 * only lays it out. The technical detail line is debug-only — it's for bug
 * reports, and in release it would just be noise the user can't act on.
 */
@Composable
private fun PortalLoadErrorPanel(
    error: PortalLoadError,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(modifier = modifier, color = MaterialTheme.colorScheme.surface) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = PortalLoadErrorText.title(error.kind),
                style = MaterialTheme.typography.titleLarge,
                textAlign = TextAlign.Center,
            )
            Text(
                text = PortalLoadErrorText.body(error),
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = 12.dp),
            )
            if (BuildConfig.DEBUG) {
                Text(
                    text = error.technicalDetail,
                    style = MaterialTheme.typography.labelSmall,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(top = 12.dp),
                )
            }
            if (PortalLoadErrorText.isRetryable(error.kind)) {
                Button(
                    onClick = onRetry,
                    modifier = Modifier.padding(top = 24.dp),
                ) {
                    Text("Try again")
                }
            }
        }
    }
}
