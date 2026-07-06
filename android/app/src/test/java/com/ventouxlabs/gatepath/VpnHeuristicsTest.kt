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

    @Test
    fun `exit node id present means full tunnel`() {
        val body = """{"BackendState":"Running","ExitNodeID":"nodeABC123"}"""
        assertTrue(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `empty exit node id means split tunnel`() {
        val body = """{"BackendState":"Running","ExitNodeID":""}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `missing exit node field means split tunnel`() {
        val body = """{"BackendState":"Running"}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `empty body means split tunnel`() {
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(""))
    }

    @Test
    fun `whitespace-formatted empty exit node id means split tunnel`() {
        // A space after the colon (`"ExitNodeID": ""`) must not evade the
        // empty-value check. The old substring match reported full-tunnel here.
        val body = """{"BackendState":"Running", "ExitNodeID": ""}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `whitespace-formatted present exit node id means full tunnel`() {
        val body = """{"BackendState":"Running", "ExitNodeID": "nodeABC123"}"""
        assertTrue(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `malformed body means split tunnel`() {
        // Not JSON at all — must fail safe rather than throw.
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel("this is not json"))
    }

    @Test
    fun `null exit node id means split tunnel`() {
        val body = """{"BackendState":"Running", "ExitNodeID": null}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }

    @Test
    fun `non-string exit node id means split tunnel`() {
        // A structurally unexpected value must not be treated as a live exit node.
        val body = """{"BackendState":"Running", "ExitNodeID": {"nested": true}}"""
        assertFalse(VpnHeuristics.tailscaleBodyIndicatesFullTunnel(body))
    }
}
