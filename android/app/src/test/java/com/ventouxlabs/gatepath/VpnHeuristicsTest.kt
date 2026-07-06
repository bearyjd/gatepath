package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.VpnHeuristics
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnHeuristicsTest {

    // ── isVpnInterfaceName ────────────────────────────────────────────────

    @Test
    fun `known vpn prefixes are detected`() {
        for (name in listOf("tun0", "tap1", "wg0", "ipsec0", "ppp0", "tailscale0", "torguard")) {
            assertTrue("$name should be detected as VPN", VpnHeuristics.isVpnInterfaceName(name))
        }
    }

    @Test
    fun `detection is case-insensitive`() {
        assertTrue(VpnHeuristics.isVpnInterfaceName("TUN0"))
        assertTrue(VpnHeuristics.isVpnInterfaceName("Tailscale0"))
        assertTrue(VpnHeuristics.isVpnInterfaceName("WG-home"))
    }

    @Test
    fun `non-vpn interfaces are not detected`() {
        for (name in listOf("wlan0", "eth0", "lo", "rmnet0", "dummy0", "radio0", "ccmni0")) {
            assertFalse("$name should NOT be detected as VPN", VpnHeuristics.isVpnInterfaceName(name))
        }
    }

    @Test
    fun `prefix must anchor at start of name`() {
        // "tun" appearing mid-name must not match
        assertFalse(VpnHeuristics.isVpnInterfaceName("virtun0"))
        assertFalse(VpnHeuristics.isVpnInterfaceName("xwg0"))
    }

    @Test
    fun `empty name is not a vpn`() {
        assertFalse(VpnHeuristics.isVpnInterfaceName(""))
    }

    // ── describeVpnInterface ──────────────────────────────────────────────

    @Test
    fun `tailscale interfaces classified as split_tunnel by default`() {
        assertEquals("tailscale0 (split_tunnel)", VpnHeuristics.describeVpnInterface("tailscale0"))
    }

    @Test
    fun `other tunnels classified as unknown mode`() {
        assertEquals("tun0 (unknown)", VpnHeuristics.describeVpnInterface("tun0"))
        assertEquals("wg0 (unknown)", VpnHeuristics.describeVpnInterface("wg0"))
    }

    @Test
    fun `descriptor preserves original interface casing`() {
        assertEquals("Tailscale0 (split_tunnel)", VpnHeuristics.describeVpnInterface("Tailscale0"))
    }

    // ── tailscaleBodyIndicatesFullTunnel ──────────────────────────────────
    // The real Tailscale localapi /v0/status reports the selected exit node
    // under a nested ExitNodeStatus object (ExitNodeStatus.ID); the field is
    // omitted entirely when no exit node is set. There is NO top-level
    // ExitNodeID field on the status response.

    @Test
    fun `active exit node means full tunnel`() {
        val body = """{"BackendState":"Running","ExitNodeStatus":{"ID":"nWxYz1234CNTRL","Online":true,"TailscaleIPs":["100.64.0.5/32"]}}"""
        assertTrue(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `selected exit node that is offline still means full tunnel`() {
        // A selected-but-unreachable exit node still routes traffic through it,
        // so the user must still be warned before a captive portal.
        val body = """{"BackendState":"Running","ExitNodeStatus":{"ID":"nWxYz1234CNTRL","Online":false}}"""
        assertTrue(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `no exit node status means split tunnel`() {
        // ExitNodeStatus is omitted (omitempty) when no exit node is selected.
        val body = """{"BackendState":"Running","Self":{"ID":"abc"}}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `exit node status with empty id means split tunnel`() {
        val body = """{"BackendState":"Running","ExitNodeStatus":{"ID":"","Online":false}}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `exit node status without id means split tunnel`() {
        val body = """{"BackendState":"Running","ExitNodeStatus":{"Online":true}}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `exit node status with null id means split tunnel`() {
        val body = """{"BackendState":"Running","ExitNodeStatus":{"ID":null}}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `whitespace-formatted exit node status is parsed structurally`() {
        val body = """{"BackendState":"Running", "ExitNodeStatus": { "ID": "node-1" }}"""
        assertTrue(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `empty body means split tunnel`() {
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(""))
    }

    @Test
    fun `malformed body means split tunnel`() {
        // Not JSON at all — must fail safe rather than throw.
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel("this is not json"))
    }

    @Test
    fun `valid json that is not an object means split tunnel`() {
        // A well-formed but non-object body exercises the jsonObject-throws path,
        // distinct from the not-JSON-at-all case; must still fail safe.
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel("[1,2,3]"))
    }

    @Test
    fun `null exit node status means split tunnel`() {
        val body = """{"BackendState":"Running","ExitNodeStatus":null}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `non-object exit node status means split tunnel`() {
        // A structurally unexpected ExitNodeStatus must not be treated as active.
        val body = """{"BackendState":"Running","ExitNodeStatus":"unexpected"}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }
}
