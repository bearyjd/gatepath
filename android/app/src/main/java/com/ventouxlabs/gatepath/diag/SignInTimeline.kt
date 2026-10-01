package com.ventouxlabs.gatepath.diag

/**
 * Gatepath's own step-by-step record of one sign-in through the system
 * handoff: what Android handed over, what the probe saw, whether the Wi-Fi
 * pin was granted, how the network was classified, which pages loaded and
 * what Gatepath told Android when the screen closed. Exported from the
 * sign-in screen's "Log" button so a field report doesn't need a USB cable
 * and `adb logcat`.
 *
 * Every line goes through [LogRedaction] on the way in, so no caller can put a
 * MAC address, token or full portal URL into an exported log by accident.
 * Pure Kotlin and thread-safe (the probe, the WebView and lifecycle callbacks
 * all record).
 */
class SignInTimeline(
    /** Android's network id for this sign-in; shown in the header. */
    val network: String,
    private val clock: () -> Long = System::currentTimeMillis,
    private val maxEvents: Int = MAX_EVENTS,
) {
    data class Event(val atMillis: Long, val text: String)

    private val events = ArrayDeque<Event>()
    private var firstAtMillis: Long? = null
    private var dropped = 0

    /** When this timeline started (its first event, or creation if none yet). */
    val startedAtMillis: Long = clock()

    @Synchronized
    fun record(text: String) {
        val now = clock()
        if (firstAtMillis == null) firstAtMillis = now
        events.addLast(Event(now, LogRedaction.redact(text)))
        while (events.size > maxEvents) {
            events.removeFirst()
            dropped++
        }
    }

    @Synchronized
    fun snapshot(): List<Event> = events.toList()

    /** The exportable text: a header, then one `+S.SSSs  event` line per event. */
    @Synchronized
    fun render(meta: BundleMeta): String = buildString {
        appendLine("Gatepath sign-in log")
        appendLine("Generated ${meta.generatedUtc} for network $network")
        appendLine("Gatepath ${meta.appVersionName} (${meta.appVersionCode}), Android ${meta.androidRelease} (SDK ${meta.androidSdkInt})")
        appendLine("URLs are reduced to hostnames only; MAC addresses are masked.")
        appendLine()
        if (dropped > 0) appendLine("($dropped earlier events dropped)")
        val origin = firstAtMillis
        if (events.isEmpty() || origin == null) {
            appendLine("(no events recorded)")
        } else {
            for (e in events) {
                val millis = e.atMillis - origin
                appendLine("+%d.%03ds  %s".format(millis / 1000, millis % 1000, e.text))
            }
        }
    }

    companion object {
        const val MAX_EVENTS = 500
    }
}
