package cc.grepon.gatepath.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import cc.grepon.gatepath.MainViewModel.NetworkStatus
import cc.grepon.gatepath.session.PortalSession

/**
 * Main status screen shown when no portal is active.
 *
 * Renders both the session phase and the latest network observation so the
 * user always sees a real status — not just "Monitoring network…" with no
 * follow-up. On a regular WiFi, [networkStatus] becomes [NetworkStatus.NoPortal]
 * within a few hundred milliseconds of launch and the description text
 * updates accordingly.
 */
@Composable
fun MainScreen(
    session: PortalSession,
    networkStatus: NetworkStatus,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "Gatepath",
            style = MaterialTheme.typography.headlineLarge,
            color = MaterialTheme.colorScheme.primary,
        )

        Spacer(modifier = Modifier.height(16.dp))

        Text(
            text = sessionStatusText(session, networkStatus),
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurface,
            textAlign = TextAlign.Center,
        )

        val detail = sessionDetailText(session, networkStatus)
        if (detail != null) {
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = detail,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
        }

        if (session is PortalSession.Monitoring || session is PortalSession.Detected) {
            Spacer(modifier = Modifier.height(24.dp))
            Button(onClick = onDismiss) {
                Text("Dismiss")
            }
        }
    }
}

/**
 * Headline status — combines the session phase with the latest network
 * observation. On a regular WiFi the user sees "Connected — no captive
 * portal" instead of an unending "Monitoring network…".
 */
private fun sessionStatusText(
    session: PortalSession,
    networkStatus: NetworkStatus,
): String = when (session) {
    is PortalSession.Idle -> "Waiting for network"
    is PortalSession.Monitoring -> when (networkStatus) {
        NetworkStatus.NoPortal -> "Connected — no captive portal"
        NetworkStatus.SignInComplete -> "Connected — sign-in complete"
        NetworkStatus.CaptiveDetected -> "Captive portal detected"
        NetworkStatus.Lost -> "Network lost"
        NetworkStatus.Unknown -> "Monitoring network…"
    }
    is PortalSession.Detected -> "Captive portal detected"
    is PortalSession.Active -> "Portal session active"
    is PortalSession.Completed -> "Session closed: ${session.closeReason.schemaValue}"
    is PortalSession.Error -> "Error: ${session.message}"
}

/**
 * Subhead detail — gives the user a sentence explaining what the app is
 * doing right now and whether they need to act.
 */
private fun sessionDetailText(
    session: PortalSession,
    networkStatus: NetworkStatus,
): String? = when (session) {
    is PortalSession.Monitoring -> when (networkStatus) {
        NetworkStatus.NoPortal ->
            "Your WiFi is fine. Gatepath will open a sign-in window if you join a captive network."
        NetworkStatus.SignInComplete ->
            "Captive portal sign-in completed."
        NetworkStatus.CaptiveDetected -> null
        NetworkStatus.Lost -> "The captive network disconnected."
        NetworkStatus.Unknown -> "Checking your current network for a captive portal…"
    }
    is PortalSession.Detected -> "Opening the portal sign-in window."
    else -> null
}
