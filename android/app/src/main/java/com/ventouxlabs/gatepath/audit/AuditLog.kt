package com.ventouxlabs.gatepath.audit

import android.util.Log
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

private const val TAG = "GatepathAudit"

/**
 * How many entries a diagnostics bundle carries. Matches `AUDIT_TAIL` in
 * `desktop/gatepath-netns-helper/packaging/collect-diagnostics.sh`, which bounds
 * the desktop bundle the same way. `AuditTailParityTest` fails if they drift:
 * the two sides already agreed on *which* fields to scrub and disagreed on *how
 * much* to send, which is the gap this constant closes.
 */
const val BUNDLE_TAIL_LIMIT = 5000

/**
 * Outcome of reading the audit log for a bundle.
 *
 * [unreadable] is reported rather than swallowed. Dropping bad lines silently
 * would let someone debug a gap in the timeline without being able to tell a
 * missing event from a dropped line.
 */
data class AuditReadResult(
    val entries: List<AuditEntry>,
    val unreadable: Int,
)

/**
 * Coroutine-safe, append-only JSONL audit log writer.
 *
 * The [AuditLogWriter] inner class accepts a [File] and is usable in plain JVM tests.
 * [AuditLog] is the Android-aware singleton that resolves [filesDir] at runtime.
 *
 * Both share the same [AuditLogWriter] implementation to guarantee schema parity.
 */
class AuditLogWriter(private val file: File) {

    private val mutex = Mutex()
    private val json = Json { encodeDefaults = true }

    // Reading is deliberately lenient where writing stays strict. A log written
    // by a newer build can carry keys this one has never heard of, and kotlinx
    // rejects unknown keys by default — which would take the whole diagnostics
    // bundle down over a field nobody needed to read. Writing is untouched, so
    // the desktop schema parity this class exists to guarantee is unaffected.
    private val readerJson = Json { encodeDefaults = true; ignoreUnknownKeys = true }

    /** Append one [AuditEntry] to the JSONL file. Coroutine-safe via [Mutex]. */
    suspend fun append(entry: AuditEntry) {
        val line = json.encodeToString(entry)
        mutex.withLock {
            file.appendText(line + "\n", Charsets.UTF_8)
        }
    }

    /**
     * Read at most [limit] most recent entries, in chronological (file) order.
     *
     * Bounded two ways, both of which matter on a phone whose log has been
     * accumulating for a year:
     *
     * 1. Only [limit] lines are ever held, via a ring buffer over a streaming
     *    read. Reading the whole file first and truncating afterwards would
     *    still spike the heap on the read itself.
     * 2. A line that fails to parse is counted and skipped, never thrown. This
     *    log is appended to by a process the OS can kill mid-write, so a
     *    truncated final line is an ordinary state — and it must not cost the
     *    user every other entry at the moment they are trying to report a
     *    problem.
     */
    fun readRecent(limit: Int = BUNDLE_TAIL_LIMIT): AuditReadResult {
        if (limit <= 0 || !file.exists()) return AuditReadResult(emptyList(), 0)

        val tail = ArrayDeque<String>()
        file.useLines(Charsets.UTF_8) { lines ->
            for (line in lines) {
                if (line.isBlank()) continue
                if (tail.size == limit) tail.removeFirst()
                tail.addLast(line)
            }
        }

        var unreadable = 0
        val entries = tail.mapNotNull { line ->
            runCatching { readerJson.decodeFromString<AuditEntry>(line) }
                .onFailure {
                    unreadable++
                    Log.w(TAG, "Skipping unreadable audit line: ${it.message}")
                }
                .getOrNull()
        }
        return AuditReadResult(entries, unreadable)
    }
}

/**
 * Android-aware audit log singleton.
 * Initialised by [GatepathApplication] with the app's [filesDir].
 */
object AuditLog {

    @Volatile
    private var writer: AuditLogWriter? = null

    /** Must be called once from Application.onCreate before any [append] call. */
    fun init(filesDir: File) {
        writer = AuditLogWriter(File(filesDir, "audit.jsonl"))
    }

    suspend fun append(entry: AuditEntry) {
        val w = writer
        if (w == null) {
            Log.e(TAG, "AuditLog.append called before init()")
            return
        }
        w.append(entry)
    }

    fun readRecent(limit: Int = BUNDLE_TAIL_LIMIT): AuditReadResult =
        writer?.readRecent(limit) ?: AuditReadResult(emptyList(), 0)
}
