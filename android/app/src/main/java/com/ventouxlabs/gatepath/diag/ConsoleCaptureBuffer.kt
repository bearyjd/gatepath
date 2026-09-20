package com.ventouxlabs.gatepath.diag

/**
 * Bounded in-memory ring buffer for [ConsoleCaptureEntry]. Pure — no
 * android.* imports or file I/O — so capacity and truncation are
 * JVM-testable. One instance per portal session (owned by the
 * GatepathWebView composable); [ConsoleCaptureFile.write] flushes a
 * [snapshot] to disk on session dispose or same-network URL change.
 */
class ConsoleCaptureBuffer(private val capacity: Int = CAPACITY) {

    private val entries = ArrayDeque<ConsoleCaptureEntry>()

    /** Oldest entry is evicted once [capacity] is reached. */
    fun record(entry: ConsoleCaptureEntry) {
        val truncated = if (entry.message.length > MAX_MESSAGE_CHARS) {
            entry.copy(message = entry.message.take(MAX_MESSAGE_CHARS) + "…")
        } else {
            entry
        }
        if (entries.size == capacity) entries.removeFirst()
        entries.addLast(truncated)
    }

    fun snapshot(): List<ConsoleCaptureEntry> = entries.toList()

    /** Called after flushing a snapshot on URL change, so the next session doesn't inherit the previous page's messages. */
    fun clear() = entries.clear()

    companion object {
        const val CAPACITY = 200
        const val MAX_MESSAGE_CHARS = 500
    }
}
