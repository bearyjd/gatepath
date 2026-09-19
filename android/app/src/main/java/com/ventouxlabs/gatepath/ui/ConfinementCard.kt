package com.ventouxlabs.gatepath.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.ventouxlabs.gatepath.network.ConfinementState

/**
 * One sentence and one action per [ConfinementState]. Replaces the old
 * troubleshooting list: a user facing a blocked sign-in needs the single next
 * step, not a ranked inventory of everything the probes noticed.
 *
 * The copy lives in [ConfinementStateText], which is JVM-tested; this
 * composable only lays it out.
 */
@Composable
fun ConfinementCard(
    state: ConfinementState,
    vpnAppLabel: String?,
    onAction: (ConfinementAction) -> Unit,
    onShareEvidence: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val action = ConfinementStateText.action(state)
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surfaceVariant,
        tonalElevation = 2.dp,
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(ConfinementStateText.sentence(state, vpnAppLabel), style = MaterialTheme.typography.bodyLarge)
            Button(onClick = { onAction(action) }) { Text(ConfinementStateText.actionLabel(action)) }
            if (action != ConfinementAction.SHARE_EVIDENCE) {
                TextButton(onClick = onShareEvidence) {
                    Text(ConfinementStateText.actionLabel(ConfinementAction.SHARE_EVIDENCE))
                }
            }
        }
    }
}
