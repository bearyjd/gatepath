package com.ventouxlabs.gatepath.diag

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Guard, not a comment: Android's regex engine is ICU, where `\b`, `\w`, `\d`
 * and `\s` are Unicode-aware, so a redaction pattern using them matches
 * differently on a device than in these JVM tests. On ICU there is no word
 * boundary in `接続mac=…` or `接続172.20.9.99`, so the identifier leaks there
 * while every JVM test passes. The patterns use explicit ASCII classes and
 * lookarounds instead.
 *
 * `\s` is allowed in [LogRedaction] only: its `redact` turns all Unicode
 * whitespace into a plain space before anything else, so `\s` sees only that
 * on both engines. [DiagnosticsBundle] has no such step.
 */
class RedactionPatternsTest {

    @Test
    fun `no sign-in log pattern relies on a unicode-dependent class`() {
        assertAsciiOnly("LogRedaction.kt", minPatterns = 7, banned = Regex("""\\[bBwWdD]"""))
    }

    @Test
    fun `no diagnostics bundle pattern relies on a unicode-dependent class`() {
        assertAsciiOnly("DiagnosticsBundle.kt", minPatterns = 6, banned = Regex("""\\[bBwWdDsS]"""))
    }

    /** Every raw-string literal in the file is a regex; a minimum count proves the scan found them. */
    private fun assertAsciiOnly(fileName: String, minPatterns: Int, banned: Regex) {
        val source = File(mainSourceRoot(), "com/ventouxlabs/gatepath/diag/$fileName").readText()
        val patterns = Regex("\"\"\"(.*?)\"\"\"", RegexOption.DOT_MATCHES_ALL).findAll(source).map { it.groupValues[1] }.toList()
        assertTrue("only ${patterns.size} patterns found in $fileName", patterns.size >= minPatterns)
        val offenders = patterns.filter { banned.containsMatchIn(it) }
        assertTrue("ICU reads these differently in $fileName: $offenders", offenders.isEmpty())
    }

    /**
     * `android/app/src/main/java`, from `-Dgatepath.repo.root` (set by
     * run-jvm-tests.sh) or by walking up from the working directory (Gradle
     * runs in android/app).
     */
    private fun mainSourceRoot(): File {
        val relative = "android/app/src/main/java"
        System.getProperty("gatepath.repo.root")?.let { root ->
            File(root, relative).takeIf { it.isDirectory }?.let { return it }
        }
        var dir: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        while (dir != null) {
            File(dir, relative).takeIf { it.isDirectory }?.let { return it }
            dir = dir.parentFile
        }
        throw AssertionError("$relative not found (set -Dgatepath.repo.root=<repo>)")
    }
}
