package com.ventouxlabs.gatepath.diag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class SignInTimelineTest {

    private var now = 1_000_000L
    private val clock = { now }

    private val meta = BundleMeta(
        generatedUtc = "2026-10-01T09:05:00Z",
        appVersionName = "1.0.1",
        appVersionCode = 101,
        androidRelease = "17",
        androidSdkInt = 37,
    )

    @Test
    fun `events render in order with time since the first event`() {
        val t = SignInTimeline(network = "183", clock = clock)
        t.record("Sign-in screen opened")
        now += 219
        t.record("Classified: confined")
        now += 1_500
        t.record("Page started: https://www.marriott.com/x?MA=123")

        val lines = t.render(meta).lines()
        val events = lines.filter { it.startsWith("+") }
        assertEquals(
            listOf(
                "+0.000s  Sign-in screen opened",
                "+0.219s  Classified: confined",
                "+1.719s  Page started: https://www.marriott.com",
            ),
            events,
        )
    }

    @Test
    fun `the header names the network and the build without identifying the device`() {
        val out = SignInTimeline(network = "183", clock = clock).apply { record("x") }.render(meta)
        assertTrue(out.contains("Gatepath sign-in log"))
        assertTrue(out.contains("network 183"))
        assertTrue(out.contains("Gatepath 1.0.1 (101)"))
        assertTrue(out.contains("Android 17 (SDK 37)"))
        assertTrue(out.contains("hostnames only"))
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
    fun `the log is capped and says how much was dropped`() {
        val t = SignInTimeline(network = "183", clock = clock, maxEvents = 3)
        repeat(5) { t.record("event $it"); now += 10 }
        val out = t.render(meta)
        assertTrue(out.contains("2 earlier events dropped"))
        assertFalse(out.contains("event 0"))
        assertTrue(out.contains("event 4"))
        assertEquals(3, out.lines().count { it.startsWith("+") })
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
    fun `a stale timeline is replaced after the window`() {
        val store = SignInTimelineStore(clock = clock, windowMillis = 30 * 60_000L)
        val first = store.forNetwork("183")
        first.record("old")
        now += 31 * 60_000L
        val second = store.forNetwork("183")
        assertNotSame(first, second)
        assertTrue(second.render(meta).contains("(no events recorded)"))
    }
}
