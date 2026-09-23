package com.ventouxlabs.gatepath.diag

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

/** File name under `filesDir`. Shared by the writer (GatepathWebView) and the reader (DiagnosticsSharer) so it's defined once. */
const val CONSOLE_CAPTURE_FILE_NAME = "webview-console.jsonl"

/**
 * Outcome of reading a flushed console-capture file. Mirrors
 * [com.ventouxlabs.gatepath.audit.AuditReadResult]'s honesty: a corrupt line
 * is counted, not silently dropped.
 *
 * [incidentId] is the first non-null [ConsoleCaptureEntry.incidentId] among
 * [entries], and it speaks for the whole file because a file cannot mix ids:
 * `GatepathWebView` captures the tag by value once, when the WebView is
 * constructed (a stated invariant there, not an accident), and every flush
 * overwrites the file with that one WebView's buffer. Null when no entry
 * carries one (an old-shape file, or a session that never had an incident).
 * If the capture site ever reads the id live, this must become mixed-id aware.
 */
data class ConsoleCaptureReadResult(val entries: List<ConsoleCaptureEntry>, val unreadable: Int) {
    val incidentId: Long? = entries.firstNotNullOfOrNull { it.incidentId }
}

/**
 * File I/O for [ConsoleCaptureEntry] snapshots: flush-on-session-end,
 * read-once-at-share-time. [ConsoleCaptureBuffer] owns the live in-memory
 * ring buffer this writes/reads a snapshot of. No coroutine/Mutex here
 * (unlike AuditLogWriter): writes are a single overwrite per session, not
 * concurrent appends.
 */
object ConsoleCaptureFile {

    private val writerJson = Json { encodeDefaults = true }
    private val readerJson = Json { encodeDefaults = true; ignoreUnknownKeys = true }

    /** Overwrites [file] with [entries] — keep only the latest session, matching DiagnosticsSharer's bundle-output behavior. */
    fun write(file: File, entries: List<ConsoleCaptureEntry>) {
        val text = entries.joinToString(separator = "") { writerJson.encodeToString(it) + "\n" }
        file.writeText(text, Charsets.UTF_8)
    }

    fun read(file: File): ConsoleCaptureReadResult {
        if (!file.exists()) return ConsoleCaptureReadResult(emptyList(), 0)

        var unreadable = 0
        val entries = file.readLines(Charsets.UTF_8)
            .filter { it.isNotBlank() }
            .mapNotNull { line ->
                runCatching { readerJson.decodeFromString<ConsoleCaptureEntry>(line) }
                    .onFailure { unreadable++ }
                    .getOrNull()
            }
        return ConsoleCaptureReadResult(entries, unreadable)
    }
}
