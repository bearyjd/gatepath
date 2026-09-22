package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.UnknownReason
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
        ConfinementState.Unknown("timeout", null, UnknownReason.PROBE_ERROR),
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
        val blockedBlank = ConfinementStateText.sentence(ConfinementState.Blocked(VpnKind.TORGUARD), "   ")
        assertTrue(blockedBlank.contains("TorGuard"))
        val tunneledEmpty = ConfinementStateText.sentence(ConfinementState.Tunnelled(VpnKind.TAILSCALE), "")
        assertTrue(tunneledEmpty.contains("Tailscale"))
    }

    @Test
    fun `dns strict names the host and never a url`() {
        val text = ConfinementStateText.sentence(all[3], null)
        assertTrue(text.contains("n143.network-auth.com"))
        assertFalse(text.contains("http"))
    }

    @Test
    fun `no state's sentence ever contains a url, query or token`() {
        val urlBearingStates = listOf(
            ConfinementState.Confined("http://10.0.0.1/login?token=secret", null),
            ConfinementState.Tunnelled(VpnKind.OTHER),
            ConfinementState.Blocked(VpnKind.NONE),
            ConfinementState.DnsStrict("n143.network-auth.com"),
            ConfinementState.Unknown(
                "connect to http://gw.example/x?token=abc failed",
                "http://other.example/?sid=1",
                UnknownReason.PROBE_ERROR,
            )
        )
        val urlMarkers = listOf("http://", "https://", "?", "token=", "sid=")

        // Test with null vpnAppLabel
        for (state in urlBearingStates) {
            val sentence = ConfinementStateText.sentence(state, vpnAppLabel = null)
            for (marker in urlMarkers) {
                assertFalse("State ${state.schemaName} should not contain '$marker' (vpnAppLabel=null)", sentence.contains(marker))
            }
        }

        // Test with URL-like vpnAppLabel (should appear once verbatim, but no URL markers leaked)
        for (state in urlBearingStates.filterIsInstance<ConfinementState.Tunnelled>() +
                      urlBearingStates.filterIsInstance<ConfinementState.Blocked>()) {
            val evilLabel = "http://evil.example"
            val sentence = ConfinementStateText.sentence(state, vpnAppLabel = evilLabel)
            assertEquals("VPN label should appear exactly once", 1, sentence.split(evilLabel).size - 1)
            // URL markers from other sources should not appear
            for (marker in listOf("10.0.0.1", "gw.example", "other.example")) {
                assertFalse("State ${state.schemaName} should not leak other URLs (vpnAppLabel=http://evil.example)", sentence.contains(marker))
            }
        }
    }

    @Test
    fun `handoff unknown without a vpn offers the sign-in page and never mentions sharing`() {
        val copy = ConfinementStateText.handoffUnknown(UnknownReason.PROBE_ERROR, VpnKind.NONE, vpnAppLabel = null)
        assertEquals(ConfinementAction.SIGN_IN_HERE, copy.action)
        assertEquals("Try signing in anyway", copy.actionLabel)
        assertFalse("the handoff screen has no share control", copy.sentence.contains("evidence"))
    }

    @Test
    fun `handoff unknown under a vpn offers the vpn app and names it like tunnelled does`() {
        val known = ConfinementStateText.handoffUnknown(UnknownReason.PROBE_ERROR, VpnKind.TAILSCALE, vpnAppLabel = "Tailscale")
        assertEquals(ConfinementAction.OPEN_VPN_APP, known.action)
        assertEquals("Open VPN app", known.actionLabel)
        assertTrue(known.sentence.contains("Exclude Gatepath in Tailscale"))
        assertFalse(known.sentence.contains("evidence"))

        val other = ConfinementStateText.handoffUnknown(UnknownReason.PROBE_ERROR, VpnKind.OTHER, vpnAppLabel = "  ")
        assertTrue(other.sentence.contains("Exclude Gatepath in your VPN app"))
    }

    @Test
    fun `handoff unknown with a validated bind offers sign-in even under a vpn`() {
        val copy = ConfinementStateText.handoffUnknown(UnknownReason.BOUND_VALIDATED, VpnKind.TAILSCALE, vpnAppLabel = "Tailscale")
        assertEquals(ConfinementAction.SIGN_IN_HERE, copy.action)
        assertEquals("Try signing in anyway", copy.actionLabel)
        assertFalse("the handoff screen has no share control", copy.sentence.contains("evidence"))
        assertFalse("a validated bind must not be told to exclude the vpn", copy.sentence.contains("Exclude Gatepath"))
    }

    @Test
    fun `every action has a label`() {
        for (a in ConfinementAction.entries) {
            assertTrue(ConfinementStateText.actionLabel(a).isNotBlank())
        }
    }
}
