package com.ventouxlabs.gatepath.diag

import java.util.Locale

/**
 * Gatepath's own step-by-step record of one sign-in through the system
 * handoff: what Android handed over, what the probe saw, whether the Wi-Fi
 * pin was granted, how the network was classified, which pages loaded and
 * what Gatepath told Android when the screen closed. Exported from the
 * sign-in screen's "Log" button so a field report doesn't need a USB cable
 * and `adb logcat`.
 *
 * Every line goes through [LogRedaction.redact] on the way in; call sites
 * also reduce known URLs with [LogRedaction.origin]. The timeline never holds
 * raw text. Pure Kotlin and thread-safe (lifecycle callbacks, the WebView and
 * the export all touch it).
 *
 * Time comes from a monotonic clock by default, so a wall-clock step (a time
 * sync right after sign-in) cannot reorder or negate offsets.
 */
class SignInTimeline(
    /** Android's network id for this sign-in; shown in the header. */
    val network: String,
    private val clock: () -> Long = MONOTONIC_MILLIS,
    private val maxEvents: Int = MAX_EVENTS,
) {
    init {
        require(maxEvents >= 2) { "maxEvents must be at least 2, was $maxEvents" }
    }

    private data class Event(val atMillis: Long, val text: String)

    /** The opening events are kept even when a loop fills the log. */
    private val headLimit = minOf(HEAD_EVENTS, maxEvents / 2)
    private val head = ArrayList<Event>()
    private val tail = ArrayDeque<Event>()
    private var dropped = 0

    /** Last time anything was recorded (or creation); [SignInTimelineStore]'s window runs from here. */
    @Volatile
    var lastActivityMillis: Long = clock()
        private set

    @Synchronized
    fun record(text: String) {
        val now = clock()
        lastActivityMillis = now
        val event = Event(now, LogRedaction.redact(text))
        if (head.size < headLimit) {
            head.add(event)
            return
        }
        tail.addLast(event)
        while (head.size + tail.size > maxEvents) {
            tail.removeFirst()
            dropped++
        }
    }

    /** The exportable text: a header, then one `+S.SSSs  event` line per event. */
    @Synchronized
    fun render(meta: BundleMeta): String = buildString {
        appendLine("Gatepath sign-in log")
        appendLine("Generated ${meta.generatedUtc} for network $network")
        appendLine("Gatepath ${meta.appVersionName} (${meta.appVersionCode}), Android ${meta.androidRelease} (SDK ${meta.androidSdkInt})")
        appendLine("Only scheme and hostname are kept from URLs; queries, MAC addresses and IP addresses are removed or masked.")
        appendLine("The hostnames that remain can identify the venue.")
        appendLine()
        val origin = head.firstOrNull()?.atMillis
        if (origin == null) {
            appendLine("(no events recorded)")
            return@buildString
        }
        head.forEach { appendLine(line(it, origin)) }
        if (dropped > 0) appendLine("($dropped events dropped here)")
        tail.forEach { appendLine(line(it, origin)) }
    }

    private fun line(e: Event, origin: Long): String {
        val millis = (e.atMillis - origin).coerceAtLeast(0)
        return String.format(Locale.ROOT, "+%d.%03ds  %s", millis / 1000, millis % 1000, e.text)
    }

    companion object {
        const val MAX_EVENTS = 500
        const val HEAD_EVENTS = 32
        val MONOTONIC_MILLIS: () -> Long = { System.nanoTime() / 1_000_000 }
    }
}
