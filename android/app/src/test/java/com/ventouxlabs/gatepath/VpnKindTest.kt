package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.VpnKind
import org.junit.Assert.assertEquals
import org.junit.Test

class VpnKindTest {
    @Test
    fun `tailscale interface wins over other names`() {
        assertEquals(VpnKind.TAILSCALE, VpnKind.fromInterfaces(listOf("tun0 (unknown)", "tailscale0 (split_tunnel)")))
    }

    @Test
    fun `torguard interface is recognised`() {
        assertEquals(VpnKind.TORGUARD, VpnKind.fromInterfaces(listOf("torguard0 (unknown)")))
    }

    @Test
    fun `any other vpn interface is OTHER`() {
        assertEquals(VpnKind.OTHER, VpnKind.fromInterfaces(listOf("wg0 (unknown)")))
        assertEquals(VpnKind.OTHER, VpnKind.fromInterfaces(listOf("tun0 (unknown)")))
    }

    @Test
    fun `no interfaces is NONE`() {
        assertEquals(VpnKind.NONE, VpnKind.fromInterfaces(emptyList()))
    }

    @Test
    fun `matching is case-insensitive and reads the descriptor prefix only`() {
        assertEquals(VpnKind.TAILSCALE, VpnKind.fromInterfaces(listOf("Tailscale0 (full_tunnel)")))
    }
}
