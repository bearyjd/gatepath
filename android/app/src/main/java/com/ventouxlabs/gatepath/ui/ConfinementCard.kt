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
    /**
     * Replaces the primary button's label without changing which
     * [ConfinementAction] it dispatches. Exists for the system-handoff
     * entry point (`CaptivePortalActivity`), which renders `Unknown` with its
     * own copy from [ConfinementStateText.handoffUnknown]; leave it null
     * everywhere else.
     */
    actionLabelOverride: String? = null,
    /**
     * Replaces the sentence for the same reason as [actionLabelOverride]: the
     * default `Unknown` sentence tells the user to share evidence, and the
     * handoff entry point has no share control to point at.
     */
    sentenceOverride: String? = null,
    /**
     * Whether to offer the secondary "Share evidence" button at all. False on
     * entry points that own no evidence bundle — `CaptivePortalActivity` has no
     * session, so the button would be a visible control that does nothing.
     * Leave it true wherever [onShareEvidence] actually shares something.
     */
    showShareEvidence: Boolean = true,
) {
    val action = ConfinementStateText.action(state)
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surfaceVariant,
        tonalElevation = 2.dp,
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(
                sentenceOverride ?: ConfinementStateText.sentence(state, vpnAppLabel),
                style = MaterialTheme.typography.bodyLarge,
            )
            Button(onClick = { onAction(action) }) {
                Text(actionLabelOverride ?: ConfinementStateText.actionLabel(action))
            }
            if (showShareEvidence && action != ConfinementAction.SHARE_EVIDENCE) {
                TextButton(onClick = onShareEvidence) {
                    Text(ConfinementStateText.actionLabel(ConfinementAction.SHARE_EVIDENCE))
                }
            }
        }
    }
}
