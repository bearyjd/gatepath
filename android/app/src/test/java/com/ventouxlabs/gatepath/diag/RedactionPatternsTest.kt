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

    private companion object {
        /** Above this, ICU's frames for a bounded count add up; today's largest is `{20}`. */
        const val MAX_BOUNDED_COUNT = 64
    }

    @Test
    fun `no sign-in log pattern relies on a unicode-dependent class`() {
        assertAsciiOnly("LogRedaction.kt", minPatterns = 7, banned = Regex("""\\[bBwWdD]"""))
    }

    @Test
    fun `no diagnostics bundle pattern relies on a unicode-dependent class`() {
        assertAsciiOnly("DiagnosticsBundle.kt", minPatterns = 6, banned = Regex("""\\[bBwWdDsS]"""))
    }

    /**
     * Guard, not a comment: these patterns run on text a gateway or a portal
     * page controls, with no length bound. Only a repetition of one bracketed
     * character class of two or more characters (`[a-z]*`, `[^\s]+`, `[\s]*`)
     * runs in constant stack on both engines. ICU pushes a backtrack frame per
     * character for a bare escape class (`\s*`), a literal (` *`), a class of
     * one character (`[ ]*`, which it compiles to a literal), an open count
     * (`[a-z]{8,}`) or a large bounded one, and fails with
     * U_REGEX_STACK_OVERFLOW after a few hundred thousand; Java recurses once
     * per repetition of a group (`(?:\.[0-9]{1,3})*`) and overflows after a
     * few thousand. Small bounded counts (`{1,3}`, `{20}`) are fine.
     */
    @Test
    fun `every unbounded repetition is a single bracketed character class`() {
        for (fileName in listOf("LogRedaction.kt", "DiagnosticsBundle.kt")) {
            val offenders = patterns(fileName).filter { unboundedNonClassRepetition(it) != null }
                .map { "$it  <- at ${unboundedNonClassRepetition(it)}" }
            assertTrue("unbounded repetition of something other than [class] in $fileName: $offenders", offenders.isEmpty())
        }
    }

    @Test
    fun `the repetition scan flags what it should`() {
        val bad = listOf(
            """ma\s*=""", """a *b""", """[a-z]{8,}""", """(?:\.[0-9]{1,3})*""", """(ab)+""", """x+""",
            """ma[ ]*=""", """[\.]*x""", """\x{41}*""", """[a-z]{0,100000}""",
        )
        for (bad in bad) {
            assertTrue("missed: $bad", unboundedNonClassRepetition(bad) != null)
        }
        val good = listOf(
            """[\s]*=""", """[a-z]{8}[a-z]*""", """[^\s"'<>()]+""", """(?:[0-9]{1,3}\.){3}""", """[*+]""", """\*\+""",
            """\p{Nd}{1,3}""", """a?""", """[ab]*""", """[^a]*""", """[a-z]{20}""",
        )
        for (good in good) {
            assertTrue("false alarm: $good", unboundedNonClassRepetition(good) == null)
        }
    }

    /** Every raw-string literal in the file is a regex; a minimum count proves the scan found them. */
    private fun assertAsciiOnly(fileName: String, minPatterns: Int, banned: Regex) {
        val patterns = patterns(fileName)
        assertTrue("only ${patterns.size} patterns found in $fileName", patterns.size >= minPatterns)
        val offenders = patterns.filter { banned.containsMatchIn(it) }
        assertTrue("ICU reads these differently in $fileName: $offenders", offenders.isEmpty())
    }

    private fun patterns(fileName: String): List<String> {
        val source = File(mainSourceRoot(), "com/ventouxlabs/gatepath/diag/$fileName").readText()
        return Regex("\"\"\"(.*?)\"\"\"", RegexOption.DOT_MATCHES_ALL).findAll(source).map { it.groupValues[1] }.toList()
    }

    /**
     * The index of the first `*`, `+`, `{n,}` or count above
     * [MAX_BOUNDED_COUNT] in [pattern] that repeats anything but a bracketed
     * class of two or more characters, or null. Escapes (`\s`, `\p{Nd}`,
     * `\x{41}`) and class contents are atoms, not syntax; a `+` or `?` right
     * after a quantifier is its modifier, and a `?` right after `(` opens a
     * group.
     */
    private fun unboundedNonClassRepetition(pattern: String): Int? {
        var i = 0
        var afterClass = false // the last atom was a bracketed class
        var afterQuantifier = false
        while (i < pattern.length) {
            val c = pattern[i]
            var next = i + 1
            var closedClass = false
            var quantifier = false
            when {
                c == '\\' -> next = if (pattern.getOrNull(i + 1) in setOf('p', 'P', 'x', 'N') && pattern.getOrNull(i + 2) == '{') {
                    pattern.indexOf('}', i) + 1
                } else {
                    i + 2
                }
                c == '[' -> {
                    next = classEnd(pattern, i) + 1
                    closedClass = !isSingleCharacter(pattern.substring(i + 1, next - 1))
                }
                (c == '*' || c == '+' || c == '?') && afterQuantifier -> {} // possessive or lazy modifier
                c == '*' || c == '+' -> if (afterClass) quantifier = true else return i
                c == '?' && pattern.getOrNull(i - 1) != '(' -> quantifier = true
                c == '{' -> {
                    val close = pattern.indexOf('}', i)
                    val body = pattern.substring(i + 1, close)
                    if (Regex("""\d+,""").matches(body)) return i
                    if ((body.substringAfter(',').toIntOrNull() ?: 0) > MAX_BOUNDED_COUNT) return i
                    next = close + 1
                    quantifier = true
                }
            }
            afterClass = closedClass || (afterClass && quantifier)
            afterQuantifier = quantifier
            i = next
        }
        return null
    }

    /** A class body naming one character (`[ ]`, `[\.]`, `[\x20]`): ICU compiles it to a literal. */
    private fun isSingleCharacter(body: String): Boolean =
        !body.startsWith("^") &&
            Regex("""[^\\]|\\[^pPsSdDwWhHvV]|\\x[0-9A-Fa-f]{2}|\\u[0-9A-Fa-f]{4}|\\x\{[0-9A-Fa-f]+\}""").matches(body)

    /** The index of the `]` closing the class that opens at [open]. */
    private fun classEnd(pattern: String, open: Int): Int {
        var i = open + 1
        if (pattern.getOrNull(i) == '^') i++
        if (pattern.getOrNull(i) == ']') i++ // a leading ] is literal
        while (pattern[i] != ']') i += if (pattern[i] == '\\') 2 else 1
        return i
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
