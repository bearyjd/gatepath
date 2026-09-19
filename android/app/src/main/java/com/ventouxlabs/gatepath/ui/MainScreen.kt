package com.ventouxlabs.gatepath.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.ventouxlabs.gatepath.R
import com.ventouxlabs.gatepath.MainViewModel.NetworkStatus
import com.ventouxlabs.gatepath.diag.DiagnosisResult
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.session.PortalSession

/**
 * Main status screen shown when no portal is active.
 *
 * Renders the session phase, the latest network observation, and — when an
 * incident has been classified — the [ConfinementCard] with the single action
 * that state calls for.
 */
@Composable
fun MainScreen(
    session: PortalSession,
    networkStatus: NetworkStatus,
    confinement: ConfinementState?,
    vpnAppLabel: String?,
    diagnosis: DiagnosisResult?,
    onDismiss: () -> Unit,
    onAction: (ConfinementAction) -> Unit,
    onRunDiagnostics: () -> Unit,
    onShareDiagnostics: (redact: Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    var showShareDialog by remember { mutableStateOf(false) }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        verticalArrangement = Arrangement.Top,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(modifier = Modifier.height(48.dp))

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

        confinement?.let { state ->
            Spacer(modifier = Modifier.height(24.dp))
            ConfinementCard(
                state = state,
                vpnAppLabel = vpnAppLabel,
                onAction = { action ->
                    if (action == ConfinementAction.SHARE_EVIDENCE) showShareDialog = true else onAction(action)
                },
                onShareEvidence = { showShareDialog = true },
            )
            if (diagnosis != null) {
                Spacer(modifier = Modifier.height(16.dp))
                DiagnosisPanel(diagnosis = diagnosis)
            }
            Spacer(modifier = Modifier.height(8.dp))
            TextButton(onClick = onRunDiagnostics) { Text("Run diagnostics again") }
        }

        if (session is PortalSession.Monitoring || session is PortalSession.Detected) {
            Spacer(modifier = Modifier.height(24.dp))
            Button(onClick = onDismiss) {
                Text("Dismiss")
            }
        }

        // Always reachable — a user needs to be able to grab a support bundle
        // regardless of the current session phase.
        Spacer(modifier = Modifier.height(24.dp))
        TextButton(onClick = { showShareDialog = true }) {
            Text(stringResource(R.string.share_diagnostics))
        }

        Spacer(modifier = Modifier.height(48.dp))
    }

    if (showShareDialog) {
        ShareDiagnosticsDialog(
            onDismiss = { showShareDialog = false },
            onConfirm = { redact ->
                showShareDialog = false
                onShareDiagnostics(redact)
            },
        )
    }
}

/**
 * Confirmation dialog for [MainScreen]'s "Share diagnostics" action. Lets the
 * user opt into (default) or out of redacting the network-identifying fields
 * before the bundle leaves the app.
 */
@Composable
private fun ShareDiagnosticsDialog(
    onDismiss: () -> Unit,
    onConfirm: (redact: Boolean) -> Unit,
) {
    var redact by remember { mutableStateOf(true) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.share_diagnostics_dialog_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(stringResource(R.string.share_diagnostics_dialog_message))
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { redact = !redact },
                ) {
                    Checkbox(checked = redact, onCheckedChange = { redact = it })
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = stringResource(R.string.share_diagnostics_redact),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(redact) }) {
                Text(stringResource(R.string.share_diagnostics_confirm))
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(stringResource(R.string.share_diagnostics_cancel))
            }
        },
    )
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
 * One-sentence summary right under the status. What to actually do about a
 * classified incident lives in [ConfinementCard]; this is just the headline.
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
