package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.ClassificationInputs
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.PortalProbeCapture
import com.ventouxlabs.gatepath.network.ProbeErrorReason
import com.ventouxlabs.gatepath.network.ProbeResult
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.network.classify
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfinementStateTest {

    private val capture = PortalProbeCapture.of(302, "text/html", PortalProbeCapture.RedirectSignal.LOCATION_HEADER)
    private val portal = ProbeResult.Portal("http://10.0.0.1/login", capture)
    private val hostPortal = ProbeResult.Portal("https://n143.network-auth.com/splash", capture)
    private val eperm = ProbeResult.Error(
        "Failed to connect to /10.0.0.1:80: connect failed: EPERM (Operation not permitted)",
        ProbeErrorReason.PERMISSION_DENIED,
    )
    private val eacces = ProbeResult.Error("connect failed: EACCES (Permission denied)", ProbeErrorReason.ACCESS_BLOCKED)
    private val timeout = ProbeResult.Error("timeout", ProbeErrorReason.TIMEOUT)

    private fun inputs(
        bound: ProbeResult,
        fallback: ProbeResult? = null,
        vpn: List<String> = emptyList(),
        strict: Boolean = false,
        resolved: Boolean? = null,
    ) = ClassificationInputs(bound, fallback, vpn, strict, resolved)

    @Test
    fun `bound portal with ip literal is Confined and carries the url`() {
        val s = classify(inputs(portal))
        assertTrue(s is ConfinementState.Confined)
        assertEquals("http://10.0.0.1/login", (s as ConfinementState.Confined).portalUrl)
        assertEquals(capture, s.capture)
    }

    @Test
    fun `bound portal with hostname that resolved on wifi is Confined`() {
        assertTrue(classify(inputs(hostPortal, resolved = true, strict = true)) is ConfinementState.Confined)
    }

    @Test
    fun `hostname portal that fails to resolve under strict private dns is DnsStrict`() {
        val s = classify(inputs(hostPortal, resolved = false, strict = true))
        assertEquals(ConfinementState.DnsStrict("n143.network-auth.com"), s)
    }

    @Test
    fun `hostname portal that fails to resolve without strict dns is Confined not DnsStrict`() {
        // The WebView will show HOST_LOOKUP_FAILED with its own copy; DnsStrict must not fire without the cause.
        assertTrue(classify(inputs(hostPortal, resolved = false, strict = false)) is ConfinementState.Confined)
    }

    @Test
    fun `EPERM on the bound probe is Tunnelled with the vpn kind`() {
        assertEquals(ConfinementState.Tunnelled(VpnKind.TAILSCALE), classify(inputs(eperm, vpn = listOf("tailscale0 (split_tunnel)"))))
        assertEquals(ConfinementState.Tunnelled(VpnKind.NONE), classify(inputs(eperm)))
    }

    @Test
    fun `EACCES on the bound probe is Blocked`() {
        assertEquals(ConfinementState.Blocked(VpnKind.TORGUARD), classify(inputs(eacces, vpn = listOf("torguard0 (unknown)"))))
    }

    @Test
    fun `a PERMISSION_DENIED reason is Tunnelled even when the message has no EPERM token`() {
        // classify() must decide from the typed reason, not the message text —
        // this message deliberately carries no "EPERM" substring.
        val err = ProbeResult.Error("libcore rendered something unrecognizable", ProbeErrorReason.PERMISSION_DENIED)
        assertEquals(ConfinementState.Tunnelled(VpnKind.NONE), classify(inputs(err)))
    }

    @Test
    fun `an ACCESS_BLOCKED reason is Blocked even when the message has no EACCES token`() {
        val err = ProbeResult.Error("libcore rendered something unrecognizable", ProbeErrorReason.ACCESS_BLOCKED)
        assertEquals(ConfinementState.Blocked(VpnKind.NONE), classify(inputs(err)))
    }

    @Test
    fun `an OTHER reason is Unknown even when the message contains EPERM`() {
        // Pinning the contract: the "EPERM"/"EACCES" substring fallback lives
        // entirely inside probeErrorReason() (see ProbeErrorReason.kt), never
        // in classify(). An Error already carrying a derived reason of OTHER
        // must not be re-parsed from its message here.
        val err = ProbeResult.Error("some other failure mentioning EPERM in passing", ProbeErrorReason.OTHER)
        assertTrue(classify(inputs(err)) is ConfinementState.Unknown)
    }

    @Test
    fun `any other bound error is Unknown and keeps both error strings`() {
        val s = classify(inputs(timeout, fallback = ProbeResult.Error("unreachable")))
        assertEquals(ConfinementState.Unknown("timeout", "unreachable"), s)
    }

    @Test
    fun `bound 204 is Unknown because a validated wifi is not an incident`() {
        val s = classify(inputs(ProbeResult.Validated))
        assertTrue(s is ConfinementState.Unknown)
    }

    @Test
    fun `every state has a distinct schema name`() {
        val names = listOf(
            ConfinementState.Confined("u", null), ConfinementState.Tunnelled(VpnKind.NONE),
            ConfinementState.Blocked(VpnKind.NONE), ConfinementState.DnsStrict("h"),
            ConfinementState.Unknown(null, null),
        ).map { it.schemaName }
        assertEquals(listOf("confined", "tunnelled", "blocked", "dns_strict", "unknown"), names)
        assertEquals(names.size, names.toSet().size)
    }
}
