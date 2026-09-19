package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.ui.ConfinementAction
import com.ventouxlabs.gatepath.ui.ConfinementStateText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfinementStateTextTest {

    private val all = listOf(
        ConfinementState.Confined("http://10.0.0.1/login", null),
        ConfinementState.Tunnelled(VpnKind.TAILSCALE),
        ConfinementState.Blocked(VpnKind.TORGUARD),
        ConfinementState.DnsStrict("n143.network-auth.com"),
        ConfinementState.Unknown("timeout", null),
    )

    @Test
    fun `every state has a non-blank single sentence`() {
        for (s in all) {
            val text = ConfinementStateText.sentence(s, vpnAppLabel = null)
            assertTrue("${s.schemaName} blank", text.isNotBlank())
            assertFalse("${s.schemaName} has a newline", text.contains('\n'))
        }
    }

    @Test
    fun `actions are fixed per state`() {
        assertEquals(ConfinementAction.SIGN_IN_HERE, ConfinementStateText.action(all[0]))
        assertEquals(ConfinementAction.OPEN_VPN_APP, ConfinementStateText.action(all[1]))
        assertEquals(ConfinementAction.OPEN_VPN_APP, ConfinementStateText.action(all[2]))
        assertEquals(ConfinementAction.OPEN_NETWORK_SETTINGS, ConfinementStateText.action(all[3]))
        assertEquals(ConfinementAction.SHARE_EVIDENCE, ConfinementStateText.action(all[4]))
    }

    @Test
    fun `tunnelled names the vpn app label when known and falls back to the kind`() {
        val withLabel = ConfinementStateText.sentence(ConfinementState.Tunnelled(VpnKind.OTHER), "Mullvad")
        assertTrue(withLabel.contains("Mullvad"))
        val fallback = ConfinementStateText.sentence(ConfinementState.Tunnelled(VpnKind.TAILSCALE), null)
        assertTrue(fallback.contains("Tailscale"))
        val generic = ConfinementStateText.sentence(ConfinementState.Blocked(VpnKind.NONE), null)
        assertTrue(generic.contains("your VPN app"))
    }

    @Test
    fun `dns strict names the host and never a url`() {
        val text = ConfinementStateText.sentence(all[3], null)
        assertTrue(text.contains("n143.network-auth.com"))
        assertFalse(text.contains("http"))
    }

    @Test
    fun `confined sentence never contains the portal url`() {
        assertFalse(ConfinementStateText.sentence(all[0], null).contains("10.0.0.1"))
    }

    @Test
    fun `every action has a label`() {
        for (a in ConfinementAction.entries) {
            assertTrue(ConfinementStateText.actionLabel(a).isNotBlank())
        }
    }
}
