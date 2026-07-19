package com.ventouxlabs.gatepath.ui

import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.ventouxlabs.gatepath.diag.DiagnosisResult
import com.ventouxlabs.gatepath.diag.DiagnosticReport
import com.ventouxlabs.gatepath.diag.RecommendedAction

/**
 * Shows the top finding from a [DiagnosisResult] plus its recommended action.
 *
 * Phase 1 surfaces:
 *   - PrivateDnsBlocking → "Open Private DNS settings"
 *   - HTTP probe Inconclusive → diagnostic info shown via the existing TroubleshootingPanel
 *   - Healthy → panel is not rendered (caller decides)
 *
 * Per D1 (confirmed 2026-05-08), the action is ALWAYS user-gated — clicking
 * the button starts the appropriate `Intent` but never auto-applies a fix.
 *
 * The mapping from action id → `Intent` lives here because Intents need
 * `Context`; the engine itself stays pure-JVM.
 */
@Composable
fun DiagnosisPanel(diagnosis: DiagnosisResult, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.tertiaryContainer,
        shape = RoundedCornerShape(12.dp),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "Most likely cause",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onTertiaryContainer,
            )
            Text(
                text = headline(diagnosis.top),
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onTertiaryContainer,
            )
            val action = diagnosis.recommended
            if (action is RecommendedAction.UserAction) {
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = action.instruction,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onTertiaryContainer,
                )
                val intent = intentFor(action.id)
                if (intent != null) {
                    Button(
                        onClick = { context.safeStart(intent) },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(buttonLabel(action.id))
                    }
                }
            }
        }
    }
}

private fun headline(report: DiagnosticReport): String = when (report) {
    is DiagnosticReport.VpnBlocking ->
        "Your VPN (${report.interfaceName}) is blocking captive sign-in"
    is DiagnosticReport.DnsHijack ->
        "DNS is being hijacked by the captive gateway"
    is DiagnosticReport.PrivateDnsBlocking ->
        "Private DNS is blocking captive sign-in"
    is DiagnosticReport.HttpProxyBlocking ->
        "An HTTP proxy is intercepting the captive redirect"
    is DiagnosticReport.SandboxedWebView ->
        "WebView routing didn't reach the captive interface"
    is DiagnosticReport.HttpsOnlyCaptive ->
        "Captive portal is blocking HTTPS"
    is DiagnosticReport.CellularFallback ->
        "Cellular is masking the captive WiFi state"
    is DiagnosticReport.NoDnsServers ->
        "The network gave no DNS servers"
    is DiagnosticReport.Inconclusive ->
        "Couldn't pinpoint the issue automatically"
    is DiagnosticReport.Healthy ->
        "Network looks healthy"
}

/**
 * Maps [RecommendedAction.Ids] to a launchable Intent. Returning null means
 * "no Intent for this action id" — the panel still shows the instruction text
 * but no button.
 */
private fun intentFor(actionId: String): Intent? = when (actionId) {
    RecommendedAction.Ids.OPEN_PRIVATE_DNS_SETTINGS ->
        Intent(Settings.ACTION_WIRELESS_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    RecommendedAction.Ids.PAUSE_VPN ->
        Intent(Settings.ACTION_VPN_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    RecommendedAction.Ids.DISABLE_HTTP_PROXY ->
        Intent(Settings.ACTION_WIFI_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    RecommendedAction.Ids.DISABLE_CELLULAR ->
        Intent(Settings.ACTION_DATA_ROAMING_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    RecommendedAction.Ids.RECONNECT_NETWORK ->
        Intent(Settings.ACTION_WIFI_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    else -> null
}

private fun buttonLabel(actionId: String): String = when (actionId) {
    RecommendedAction.Ids.OPEN_PRIVATE_DNS_SETTINGS -> "Open Wireless settings"
    RecommendedAction.Ids.PAUSE_VPN -> "Open VPN settings"
    RecommendedAction.Ids.DISABLE_HTTP_PROXY -> "Open Wi-Fi settings"
    RecommendedAction.Ids.DISABLE_CELLULAR -> "Open Cellular settings"
    RecommendedAction.Ids.RECONNECT_NETWORK -> "Open Wi-Fi settings"
    else -> "Open Settings"
}

private fun Context.safeStart(intent: Intent) {
    runCatching { startActivity(intent) }
}
