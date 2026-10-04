package com.ventouxlabs.gatepath.session

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionIncidentStateTest {
    private val portalUrl = "http://portal.example.com/login"

    @Test
    fun `accepted reenter latches confined state and its incident id`() {
        val state = SessionIncidentState()
        val accepted = PortalSessionManager.ReenterResult.Accepted(PortalSession.Detected(portalUrl))

        assertTrue(state.recordReenter(accepted, 42L))
        assertEquals(SessionIncidentState.Value(wasConfined = true, incidentId = 42L), state.value)
    }

    @Test
    fun `rejected reenter leaves a running session attribution untouched`() {
        val state = SessionIncidentState()
        state.recordReenter(PortalSessionManager.ReenterResult.Accepted(PortalSession.Detected(portalUrl)), 42L)

        assertFalse(state.recordReenter(PortalSessionManager.ReenterResult.Rejected(PortalSession.Active(portalUrl, "2026-10-04T00:00:00Z")), 99L))
        assertEquals(SessionIncidentState.Value(wasConfined = true, incidentId = 42L), state.value)
    }

    @Test
    fun `every named terminal transition clears both confinement and incident id`() {
        SessionIncidentState.TerminalTransition.entries.forEach { terminal ->
            val state = SessionIncidentState()
            state.recordReenter(PortalSessionManager.ReenterResult.Accepted(PortalSession.Detected(portalUrl)), 42L)

            state.transition(terminal)

            assertEquals("$terminal must not leave stale session attribution", SessionIncidentState.Value(), state.value)
        }
    }
}
