package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.ClassificationInputs
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.PortalProbeCapture
import com.ventouxlabs.gatepath.network.ProbeErrorReason
import com.ventouxlabs.gatepath.network.ProbeResult
import com.ventouxlabs.gatepath.network.UnknownReason
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.network.androidPortalVerdict
import com.ventouxlabs.gatepath.network.classify
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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
        systemPortalUrl: String? = null,
        flagsCaptive: Boolean = false,
        bindHeld: Boolean = false,
    ) = ClassificationInputs(bound, fallback, vpn, strict, resolved, systemPortalUrl, flagsCaptive, bindHeld)

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
    fun `an OTHER reason is Unknown with a PROBE_ERROR reason even when the message contains EPERM`() {
        // Pinning the contract: the "EPERM"/"EACCES" substring fallback lives
        // entirely inside probeErrorReason() (see ProbeErrorReason.kt), never
        // in classify(). An Error already carrying a derived reason of OTHER
        // must not be re-parsed from its message here.
        val err = ProbeResult.Error("some other failure mentioning EPERM in passing", ProbeErrorReason.OTHER)
        val s = classify(inputs(err))
        assertTrue(s is ConfinementState.Unknown)
        assertEquals(UnknownReason.PROBE_ERROR, (s as ConfinementState.Unknown).reason)
    }

    @Test
    fun `any other bound error is Unknown, keeps both error strings, and carries a PROBE_ERROR reason`() {
        val s = classify(inputs(timeout, fallback = ProbeResult.Error("unreachable")))
        assertEquals(ConfinementState.Unknown("timeout", "unreachable", UnknownReason.PROBE_ERROR), s)
    }

    @Test
    fun `bound 204 is Unknown with a BOUND_VALIDATED reason because a validated wifi is not an incident`() {
        val s = classify(inputs(ProbeResult.Validated))
        assertTrue(s is ConfinementState.Unknown)
        assertEquals(UnknownReason.BOUND_VALIDATED, (s as ConfinementState.Unknown).reason)
    }

    // ── System handoff: Android saw a portal, our probe got 204 ─────────────
    // Field case (2026-10-01, hotel portal): the gateway's walled garden lets
    // connectivitycheck.gstatic.com through before sign-in, so the bound probe
    // answered 204 while Android's own probe (another endpoint) saw the portal
    // and launched the handoff with its sign-in URL.

    private val androidPortalUrl = "https://portal.example-hotel.net/login"

    @Test
    fun `bound 204 with Android's portal url and the bind held is Confined on Android's url`() {
        val s = classify(inputs(ProbeResult.Validated, systemPortalUrl = androidPortalUrl, flagsCaptive = true, bindHeld = true))
        assertEquals(ConfinementState.Confined(androidPortalUrl, null), s)
    }

    @Test
    fun `bound 204 with Android's portal url but no process bind stays Unknown`() {
        // Without the process-wide bind the WebView would not be confined to the Wi-Fi.
        val s = classify(inputs(ProbeResult.Validated, systemPortalUrl = androidPortalUrl, flagsCaptive = true, bindHeld = false))
        assertEquals(UnknownReason.BOUND_VALIDATED, (s as ConfinementState.Unknown).reason)
    }

    @Test
    fun `bound 204 with the bind held but no Android portal url stays Unknown`() {
        // The monitor path never has Android's verdict; it is unchanged.
        val s = classify(inputs(ProbeResult.Validated, bindHeld = true))
        assertEquals(UnknownReason.BOUND_VALIDATED, (s as ConfinementState.Unknown).reason)
    }

    @Test
    fun `Android's portal host that fails to resolve under strict private dns is DnsStrict`() {
        val s = classify(
            inputs(ProbeResult.Validated, strict = true, resolved = false, systemPortalUrl = androidPortalUrl, flagsCaptive = true, bindHeld = true),
        )
        assertEquals(ConfinementState.DnsStrict("portal.example-hotel.net"), s)
    }

    @Test
    fun `a bound probe error is not rescued by Android's portal url`() {
        // Only a 204 proves the probe's socket reached the network over the Wi-Fi.
        val s = classify(inputs(timeout, systemPortalUrl = androidPortalUrl, flagsCaptive = true, bindHeld = true))
        assertEquals(UnknownReason.PROBE_ERROR, (s as ConfinementState.Unknown).reason)
    }

    @Test
    fun `EPERM under a VPN stays Tunnelled even with Android's portal url`() {
        val s = classify(inputs(eperm, vpn = listOf("tun0"), systemPortalUrl = androidPortalUrl, flagsCaptive = true, bindHeld = true))
        assertTrue(s is ConfinementState.Tunnelled)
    }

    @Test
    fun `an intent url is not trusted unless Android flags the network captive`() {
        // The handoff activity is exported: a spoofed intent on a normal,
        // validated network must not open its URL.
        val s = classify(inputs(ProbeResult.Validated, systemPortalUrl = androidPortalUrl, flagsCaptive = false, bindHeld = true))
        assertEquals(UnknownReason.BOUND_VALIDATED, (s as ConfinementState.Unknown).reason)
    }

    @Test
    fun `a non-web intent url is never trusted`() {
        for (url in listOf("javascript:alert(1)", "file:///sdcard/x.html", "", "https://", "not a url")) {
            val s = classify(inputs(ProbeResult.Validated, systemPortalUrl = url, flagsCaptive = true, bindHeld = true))
            assertTrue("$url should not be Confined", s is ConfinementState.Unknown)
        }
    }

    @Test
    fun `androidPortalVerdict needs the captive flag and an http(s) url with a host`() {
        assertEquals(androidPortalUrl, androidPortalVerdict(androidPortalUrl, systemFlagsCaptive = true))
        assertEquals("http://10.0.0.1/login", androidPortalVerdict("http://10.0.0.1/login", systemFlagsCaptive = true))
        assertNull(androidPortalVerdict(androidPortalUrl, systemFlagsCaptive = false))
        assertNull(androidPortalVerdict(null, systemFlagsCaptive = true))
        assertNull(androidPortalVerdict("javascript:alert(1)", systemFlagsCaptive = true))
        assertNull(androidPortalVerdict("ftp://portal.example-hotel.net/", systemFlagsCaptive = true))
    }

    @Test
    fun `a probe redirect still wins over Android's url and keeps its capture`() {
        val s = classify(inputs(portal, systemPortalUrl = androidPortalUrl, flagsCaptive = true, bindHeld = true))
        assertEquals(ConfinementState.Confined("http://10.0.0.1/login", capture), s)
    }

    @Test
    fun `bound 204 with Android's verdict is Confined even with a VPN interface up`() {
        // Gatepath excluded from the VPN: the bind was granted, so it is confined.
        val s = classify(
            inputs(ProbeResult.Validated, vpn = listOf("tun0"), systemPortalUrl = androidPortalUrl, flagsCaptive = true, bindHeld = true),
        )
        assertEquals(ConfinementState.Confined(androidPortalUrl, null), s)
    }

    @Test
    fun `Android's portal host that resolves under strict private dns is Confined`() {
        val s = classify(
            inputs(ProbeResult.Validated, strict = true, resolved = true, systemPortalUrl = androidPortalUrl, flagsCaptive = true, bindHeld = true),
        )
        assertTrue(s is ConfinementState.Confined)
    }

    @Test
    fun `an ip-literal Android url under strict private dns is Confined not DnsStrict`() {
        val s = classify(
            inputs(ProbeResult.Validated, strict = true, resolved = null, systemPortalUrl = "http://10.0.0.1/login", flagsCaptive = true, bindHeld = true),
        )
        assertEquals(ConfinementState.Confined("http://10.0.0.1/login", null), s)
    }

    @Test
    fun `every state has a distinct schema name`() {
        val names = listOf(
            ConfinementState.Confined("u", null), ConfinementState.Tunnelled(VpnKind.NONE),
            ConfinementState.Blocked(VpnKind.NONE), ConfinementState.DnsStrict("h"),
            ConfinementState.Unknown(null, null, UnknownReason.PROBE_ERROR),
        ).map { it.schemaName }
        assertEquals(listOf("confined", "tunnelled", "blocked", "dns_strict", "unknown"), names)
        assertEquals(names.size, names.toSet().size)
    }
}
