package com.ventouxlabs.gatepath.session

/**
 * The incident attribution latched when a portal session is accepted.
 *
 * This deliberately contains no Android or coroutine dependency so the
 * controller's most security-sensitive lifecycle invariant can be exercised
 * on the JVM: rejected re-entry never retags an existing session, and every
 * terminal path clears the tag before another session can begin.
 */
class SessionIncidentState {
    data class Value(val wasConfined: Boolean = false, val incidentId: Long = NO_INCIDENT_ID)

    /** Terminal controller transitions that must not leak attribution forward. */
    enum class TerminalTransition {
        Timeout,
        Dismissed,
        SignInSucceeded,
        NetworkClosed,
        DebugForceActive,
    }

    var value: Value = Value()
        private set

    /** Records a transition only when [result] actually opened a new session. */
    fun recordReenter(result: PortalSessionManager.ReenterResult, incidentId: Long): Boolean {
        if (result !is PortalSessionManager.ReenterResult.Accepted) return false
        value = Value(wasConfined = true, incidentId = incidentId)
        return true
    }

    /**
     * Clears attribution after a named terminal or debug-only controller path.
     * Keeping the exhaustive mapping here makes a newly added path a JVM-test
     * decision rather than an unreviewed copy of `incidentId = 0L`.
     */
    fun transition(terminal: TerminalTransition) {
        when (terminal) {
            TerminalTransition.Timeout,
            TerminalTransition.Dismissed,
            TerminalTransition.SignInSucceeded,
            TerminalTransition.NetworkClosed,
            TerminalTransition.DebugForceActive,
            -> value = Value()
        }
    }

    companion object {
        const val NO_INCIDENT_ID = 0L
    }
}
