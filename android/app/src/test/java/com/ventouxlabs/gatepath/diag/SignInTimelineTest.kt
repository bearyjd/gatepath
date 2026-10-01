package com.ventouxlabs.gatepath.diag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Locale

class SignInTimelineTest {

    private var now = 1_000_000L
    private val clock = { now }

    private val meta = BundleMeta(
        generatedUtc = "2026-10-01T09:05:00Z",
        appVersionName = "1.1.0",
        appVersionCode = 3,
        androidRelease = "17",
        androidSdkInt = 37,
    )

    private fun events(t: SignInTimeline) = t.render(meta).lines().filter { it.startsWith("+") }

    @Test
    fun `events render in order with time since the first event`() {
        val t = SignInTimeline(network = "183", clock = clock)
        t.record("Sign-in screen opened")
        now += 219
        t.record("Classified: confined")
        now += 1_500
        t.record("Page started: https://www.marriott.com/x?MA=123")

        assertEquals(
            listOf(
                "+0.000s  Sign-in screen opened",
                "+0.219s  Classified: confined",
                "+1.719s  Page started: https://www.marriott.com",
            ),
            events(t),
        )
    }

    @Test
    fun `a clock that steps backwards never renders a negative offset`() {
        val t = SignInTimeline(network = "183", clock = clock)
        t.record("first")
        now -= 1_234
        t.record("second")
        assertEquals("+0.000s  second", events(t)[1])
    }

    @Test
    fun `offsets use ascii digits whatever the device locale`() {
        val saved = Locale.getDefault()
        try {
            Locale.setDefault(Locale.forLanguageTag("ar-EG"))
            val t = SignInTimeline(network = "183", clock = clock)
            t.record("a")
            now += 1_234
            t.record("b")
            assertEquals("+1.234s  b", events(t)[1])
        } finally {
            Locale.setDefault(saved)
        }
    }

    @Test
    fun `the header names the network and the build and states what was redacted`() {
        val out = SignInTimeline(network = "183", clock = clock).apply { record("x") }.render(meta)
        assertTrue(out.contains("Gatepath sign-in log"))
        assertTrue(out.contains("network 183"))
        assertTrue(out.contains("Gatepath 1.1.0 (3)"))
        assertTrue(out.contains("Android 17 (SDK 37)"))
        assertTrue(out.contains("hostnames"))
    }

    @Test
    fun `every recorded line is redacted on the way in`() {
        val t = SignInTimeline(network = "183", clock = clock)
        t.record("Handling portal at https://guest:pw@zqqwqihz.gatewayauth.com/login?MA=12%3A34 for 12:34:56:78:9a:bc")
        val out = t.render(meta)
        assertFalse(out.contains("pw@"))
        assertFalse(out.contains("MA="))
        assertFalse(out.contains("12:34:56:78:9a:bc"))
        assertTrue(out.contains("https://zqqwqihz.gatewayauth.com"))
    }

    @Test
    fun `the cap keeps the first events and says where it dropped`() {
        val t = SignInTimeline(network = "183", clock = clock, maxEvents = 4)
        repeat(6) { t.record("event $it"); now += 10 }
        val lines = t.render(meta).lines()
        // The opening lines (opened, pin, probe, classified) are what explain a loop that fills the log.
        assertTrue(lines.any { it.endsWith("event 0") } && lines.any { it.endsWith("event 1") })
        assertFalse(lines.any { it.endsWith("event 2") } || lines.any { it.endsWith("event 3") })
        assertTrue(lines.any { it.endsWith("event 4") } && lines.any { it.endsWith("event 5") })
        assertTrue(lines.contains("(2 events dropped here)"))
        assertEquals(4, events(t).size)
    }

    @Test
    fun `an empty log says so instead of rendering nothing`() {
        assertTrue(SignInTimeline(network = "183", clock = clock).render(meta).contains("(no events recorded)"))
    }

    // ── Store: one timeline per sign-in, surviving rebuilds and reopenings ──

    @Test
    fun `the same network within the window continues the same timeline`() {
        val store = SignInTimelineStore(clock = clock)
        val first = store.forNetwork("183")
        now += 60_000
        assertSame(first, store.forNetwork("183"))
    }

    @Test
    fun `a different network starts a new timeline`() {
        val store = SignInTimelineStore(clock = clock)
        val first = store.forNetwork("183")
        assertNotSame(first, store.forNetwork("184"))
    }

    @Test
    fun `the window runs from the last activity, not from creation`() {
        val store = SignInTimelineStore(clock = clock, windowMillis = 30 * 60_000L)
        val first = store.forNetwork("183")
        now += 29 * 60_000L
        first.record("still signing in")
        now += 2 * 60_000L // 31 minutes after creation, 2 after the last event
        assertSame(first, store.forNetwork("183"))
    }

    @Test
    fun `a timeline idle past the window is replaced`() {
        val store = SignInTimelineStore(clock = clock, windowMillis = 30 * 60_000L)
        val first = store.forNetwork("183")
        first.record("old")
        now += 31 * 60_000L
        val second = store.forNetwork("183")
        assertNotSame(first, second)
        assertTrue(second.render(meta).contains("(no events recorded)"))
    }

    // ── Guard: every message the app records, with hostile values ──────────

    /**
     * Mirrors each record() template in CaptivePortalActivity and
     * GatepathWebView, fed the worst values those call sites can see. If a
     * template is added there, add it here: this file leaves the device.
     */
    @Test
    fun `no recorded message template leaks a device identifier`() {
        val mac = "12:34:56:78:9a:bc"
        val relativeLocation = "/login.php?MA=12%3A34%3A56%3A78%3A9A%3ABC&SIP=172.20.9.99&tok=s3cr3t"
        val fullUrl = "https://guest:pw@zqqwqihz.gatewayauth.com/login?MA=$mac&SIP=172.20.9.99&tok=s3cr3t"
        val intentUrl = "intent://scan/#Intent;S.browser_fallback_url=https%3A%2F%2Fx%2F%3Ftok%3Ds3cr3t;end"
        val t = SignInTimeline(network = "183", clock = clock)
        listOf(
            "Sign-in screen opened by Android for network 183 (Android's URL: ${LogRedaction.origin(fullUrl)})",
            "Probe of http://connectivitycheck.gstatic.com/generate_204 over the Wi-Fi: portal at ${LogRedaction.origin(relativeLocation)} (HTTP 302)",
            "Probe of http://connectivitycheck.gstatic.com/generate_204 over the Wi-Fi: error TIMEOUT: " +
                "failed to connect to connectivitycheck.gstatic.com/142.251.13.94 (port 80) from /172.20.9.99 (port 41234)",
            "Showing the login page: ${LogRedaction.origin(fullUrl)}",
            "Page started: ${LogRedaction.origin(fullUrl)}",
            "Page finished: ${LogRedaction.origin(relativeLocation)}",
            "Page failed to load: ${LogRedaction.origin(intentUrl)} (code=-10 net::ERR_UNKNOWN_URL_SCHEME)",
            "Followed off-domain navigation to ${LogRedaction.origin(intentUrl)}",
            "Certificate refused for zqqwqihz.gatewayauth.com (primaryError=3)",
        ).forEach { t.record(it) }
        val out = t.render(meta)
        for (secret in listOf(mac, "12%3A34", "MA=", "SIP", "172.20.9.99", "s3cr3t", "tok", "pw@", "browser_fallback", "?")) {
            assertFalse("leaked '$secret' in:\n$out", out.contains(secret))
        }
    }
}
