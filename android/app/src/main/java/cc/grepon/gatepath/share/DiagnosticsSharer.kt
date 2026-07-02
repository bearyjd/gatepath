package cc.grepon.gatepath.share

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.content.FileProvider
import cc.grepon.gatepath.audit.AuditLog
import cc.grepon.gatepath.diag.BundleMeta
import cc.grepon.gatepath.diag.DiagnosisResult
import cc.grepon.gatepath.diag.DiagnosticsBundle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.time.Instant
import java.time.format.DateTimeFormatter

/**
 * Android glue for the in-app "Share Diagnostics" flow: gathers the audit log
 * and latest [DiagnosisResult], asks the pure [DiagnosticsBundle] builder to
 * assemble the text (applying redaction there), writes it to a FileProvider-
 * shareable location, and builds the `ACTION_SEND` intent.
 *
 * The interesting logic — bundle assembly and redaction — is intentionally NOT
 * here; it lives in the JVM-tested [DiagnosticsBundle]. This object only does
 * the platform I/O that can't run without an Android SDK.
 */
object DiagnosticsSharer {

    /** Cache subdir declared in res/xml/file_paths.xml. */
    private const val CACHE_SUBDIR = "diagnostics"
    private const val FILE_NAME = "gatepath-diagnostics.txt"
    const val MIME_TYPE = "text/plain"

    /** Authority must match the `<provider>` in AndroidManifest.xml. */
    private fun authority(context: Context): String = "${context.packageName}.fileprovider"

    /**
     * Builds the diagnostics bundle and returns a content:// [Uri] to it,
     * readable by the app the user chooses in the share sheet.
     *
     * Runs its file I/O off the main thread.
     */
    suspend fun writeBundle(
        context: Context,
        diagnosis: DiagnosisResult?,
        redact: Boolean,
    ): Uri = withContext(Dispatchers.IO) {
        val text = DiagnosticsBundle.build(
            meta = collectMeta(context),
            entries = AuditLog.readAll(),
            diagnosis = diagnosis,
            redact = redact,
        )

        val dir = File(context.cacheDir, CACHE_SUBDIR)
        // Keep only the latest bundle so the cache doesn't accumulate copies.
        dir.deleteRecursively()
        dir.mkdirs()
        val file = File(dir, FILE_NAME)
        file.writeText(text, Charsets.UTF_8)

        FileProvider.getUriForFile(context, authority(context), file)
    }

    /** The share-sheet intent for a bundle [uri] produced by [writeBundle]. */
    fun sendIntent(uri: Uri, subject: String): Intent =
        Intent(Intent.ACTION_SEND).apply {
            type = MIME_TYPE
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_SUBJECT, subject)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }

    private fun collectMeta(context: Context): BundleMeta {
        val pkg = context.packageName
        val info = runCatching {
            context.packageManager.getPackageInfo(pkg, 0)
        }.getOrNull()
        // minSdk 29 ≥ P (28), so longVersionCode is always available.
        val versionCode = info?.longVersionCode ?: 0L

        return BundleMeta(
            generatedUtc = DateTimeFormatter.ISO_INSTANT.format(Instant.now()),
            appVersionName = info?.versionName ?: "unknown",
            appVersionCode = versionCode,
            androidRelease = Build.VERSION.RELEASE ?: "unknown",
            androidSdkInt = Build.VERSION.SDK_INT,
        )
    }
}
