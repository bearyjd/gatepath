package cc.grepon.gatepath.audit

import android.util.Log
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

private const val TAG = "GatepathAudit"

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

    /** Append one [AuditEntry] to the JSONL file. Coroutine-safe via [Mutex]. */
    suspend fun append(entry: AuditEntry) {
        val line = json.encodeToString(entry)
        mutex.withLock {
            file.appendText(line + "\n", Charsets.UTF_8)
        }
    }

    /**
     * Read all entries in chronological (file) order.
     * Returns an empty list if the file does not yet exist.
     */
    fun readAll(): List<AuditEntry> {
        if (!file.exists()) return emptyList()
        return file.readLines(Charsets.UTF_8)
            .filter { it.isNotBlank() }
            .map { json.decodeFromString<AuditEntry>(it) }
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

    fun readAll(): List<AuditEntry> = writer?.readAll() ?: emptyList()
}
