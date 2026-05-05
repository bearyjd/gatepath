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
import androidx.compose.ui.unit.dp
import cc.grepon.gatepath.session.PortalSession

/**
 * Main status screen shown when no portal is active.
 * Displays the current session state and provides a manual dismiss option.
 */
@Composable
fun MainScreen(
    session: PortalSession,
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
            text = sessionStatusText(session),
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurface,
        )

        if (session is PortalSession.Monitoring || session is PortalSession.Detected) {
            Spacer(modifier = Modifier.height(24.dp))
            Button(onClick = onDismiss) {
                Text("Dismiss")
            }
        }
    }
}

private fun sessionStatusText(session: PortalSession): String = when (session) {
    is PortalSession.Idle -> "Waiting for captive portal network…"
    is PortalSession.Monitoring -> "Monitoring network…"
    is PortalSession.Detected -> "Portal detected at ${session.portalUrl}"
    is PortalSession.Active -> "Portal session active"
    is PortalSession.Completed -> "Session closed: ${session.closeReason.schemaValue}"
    is PortalSession.Error -> "Error: ${session.message}"
}
