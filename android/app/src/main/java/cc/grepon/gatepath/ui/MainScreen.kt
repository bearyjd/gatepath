package cc.grepon.gatepath.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import cc.grepon.gatepath.MainViewModel.NetworkStatus
import cc.grepon.gatepath.network.NetworkDiagnostics
import cc.grepon.gatepath.session.PortalSession

/**
 * Main status screen shown when no portal is active.
 *
 * Renders the session phase, the latest network observation, and — when the
 * monitor flagged the network as captive but couldn't probe — a structured
 * troubleshooting pathway with recovery steps and raw diagnostics.
 */
@Composable
fun MainScreen(
    session: PortalSession,
    networkStatus: NetworkStatus,
    diagnostics: NetworkDiagnostics?,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
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

        if (networkStatus == NetworkStatus.CaptivePending && diagnostics != null) {
            Spacer(modifier = Modifier.height(24.dp))
            TroubleshootingPanel(diagnostics)
        }

        if (session is PortalSession.Monitoring || session is PortalSession.Detected) {
            Spacer(modifier = Modifier.height(24.dp))
            Button(onClick = onDismiss) {
                Text("Dismiss")
            }
        }
        Spacer(modifier = Modifier.height(48.dp))
    }
}

@Composable
private fun TroubleshootingPanel(diagnostics: NetworkDiagnostics) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surfaceVariant,
        tonalElevation = 2.dp,
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                "Troubleshooting",
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            val steps = recoverySteps(diagnostics)
            steps.forEachIndexed { i, step ->
                Text(
                    "${i + 1}. $step",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))

            Text(
                "Diagnostics",
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            DiagnosticRow("Network", diagnostics.networkId)
            DiagnosticRow(
                "VPN",
                if (diagnostics.vpnInterfaces.isEmpty()) "none"
                else diagnostics.vpnInterfaces.joinToString(", "),
            )
            if (diagnostics.isTailscaleFullTunnel) {
                DiagnosticRow("Tailscale exit node", "active (full tunnel)")
            }
            DiagnosticRow(
                "Private DNS",
                when {
                    !diagnostics.privateDnsActive -> "off / auto"
                    diagnostics.privateDnsServer != null -> "strict: ${diagnostics.privateDnsServer}"
                    else -> "active (auto)"
                },
            )
            DiagnosticRow("HTTP proxy", diagnostics.httpProxyDescription ?: "none")
            DiagnosticRow("DNS servers", diagnostics.dnsServerCount.toString())
            if (diagnostics.bindProbeError != null) {
                DiagnosticRow("Bind probe error", diagnostics.bindProbeError)
            }
            if (diagnostics.fallbackProbeError != null) {
                DiagnosticRow("Fallback probe", diagnostics.fallbackProbeError)
            }
        }
    }
}

@Composable
private fun DiagnosticRow(label: String, value: String) {
    Text(
        text = "$label: $value",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/**
 * Recovery-step list ordered strongest signal first. The user reads top-down
 * and stops as soon as they understand the situation.
 */
private fun recoverySteps(d: NetworkDiagnostics): List<String> {
    val steps = mutableListOf<String>()

    if (d.isTailscaleFullTunnel) {
        steps += "Tailscale is using an exit node. The exit node hijacks your default route, " +
            "so the captive portal sign-in can't reach the local gateway. Disable the exit " +
            "node in Tailscale, then re-open Gatepath."
    } else if (d.vpnInterfaces.isNotEmpty()) {
        steps += "A VPN is active (${d.vpnInterfaces.joinToString(", ")}). " +
            "Pause it temporarily so the sign-in can route via the captive Wi-Fi, " +
            "or skip to step 2 to use the system handoff (works without pausing the VPN)."
    }

    steps += "Pull down notifications and tap \"Sign in to Wi-Fi network.\" " +
        "Pick Gatepath in the chooser. The system delivers a sign-in token that bypasses " +
        "the OS restriction on captive networks."

    if (d.privateDnsActive) {
        val server = d.privateDnsServer
        steps += if (server != null) {
            "Private DNS is set to \"$server\" (strict). Captive portals commonly block DoT/DoH. " +
                "If sign-in fails, set Private DNS to \"Off\" in Settings → Network → Private DNS, " +
                "complete the sign-in, then turn it back on."
        } else {
            "Private DNS is on (auto / opportunistic). Captive portals can block this. " +
                "If sign-in fails, set Private DNS to \"Off,\" complete sign-in, then re-enable."
        }
    }

    if (d.httpProxyDescription != null) {
        steps += "An HTTP proxy is configured for this Wi-Fi (${d.httpProxyDescription}). " +
            "Captive portal sign-ins usually bypass the proxy. If sign-in fails, " +
            "remove the proxy in Wi-Fi settings just for this network."
    }

    if (d.dnsServerCount == 0) {
        steps += "The network advertised no DNS servers — DHCP may have failed. Forget " +
            "and rejoin the Wi-Fi network."
    }

    return steps
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
        NetworkStatus.CaptivePending -> "Captive portal — sign-in needed"
        NetworkStatus.Lost -> "Network lost"
        NetworkStatus.Unknown -> "Monitoring network…"
    }
    is PortalSession.Detected -> "Captive portal detected"
    is PortalSession.Active -> "Portal session active"
    is PortalSession.Completed -> "Session closed: ${session.closeReason.schemaValue}"
    is PortalSession.Error -> "Error: ${session.message}"
}

/**
 * One-sentence summary right under the status. The detailed step list lives
 * in [TroubleshootingPanel] for CaptivePending; this is just the headline.
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
        NetworkStatus.CaptivePending ->
            "This network has a captive portal but probing is blocked. See troubleshooting below."
        NetworkStatus.Lost -> "The captive network disconnected."
        NetworkStatus.Unknown -> "Checking your current network for a captive portal…"
    }
    is PortalSession.Detected -> "Opening the portal sign-in window."
    else -> null
}
