package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.CaptivePortalReply
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * What the system handoff ([CaptivePortalActivity]) tells Android when the
 * sign-in screen goes away.
 *
 * Regression for the 2026-09-30 field run on a hotel captive portal: after a
 * successful sign-in, leaving the screen with the back gesture made the
 * activity answer `ignoreNetwork()`, and Android dropped the Wi-Fi one second
 * later and disabled auto-join for it. Only the Dismiss button avoided that.
 */
class CaptivePortalReplyTest {

    @Test
    fun `leaving the screen without an answer asks Android to re-check the network`() {
        assertEquals(
            CaptivePortalReply.DISMISSED,
            CaptivePortalReply.onDestroy(alreadyReported = false, changingConfigurations = false),
        )
    }

    @Test
    fun `no second answer after the Dismiss button already sent one`() {
        assertEquals(
            CaptivePortalReply.NONE,
            CaptivePortalReply.onDestroy(alreadyReported = true, changingConfigurations = false),
        )
    }

    @Test
    fun `a fold or rotation rebuild says nothing so the rebuilt screen can answer`() {
        assertEquals(
            CaptivePortalReply.NONE,
            CaptivePortalReply.onDestroy(alreadyReported = false, changingConfigurations = true),
        )
    }

    @Test
    fun `there is no ignore-the-network answer`() {
        assertEquals(
            setOf("DISMISSED", "NONE"),
            CaptivePortalReply.entries.map { it.name }.toSet(),
        )
    }

    /**
     * Guard, not a comment: `ignoreNetwork()` tells Android the user rejected
     * the network, which tears the Wi-Fi down and disables auto-join. Nothing
     * in the app is that decision, so no code may send it — called directly
     * or passed as a method reference (`CaptivePortal::ignoreNetwork`).
     * Comment lines are skipped so KDoc can still name the API.
     */
    @Test
    fun `no app code sends ignoreNetwork`() {
        val mainRoot = mainSourceRoot()
        val offenders = mainRoot.walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .flatMap { file ->
                file.readLines()
                    .map { it.substringBefore("//").trim() }
                    .filterNot { it.startsWith("*") || it.startsWith("/*") }
                    .filter { Regex("""\bignoreNetwork\b""").containsMatchIn(it) }
                    .map { "${file.relativeTo(mainRoot)}: $it" }
            }
            .toList()
        assertTrue("app code sends ignoreNetwork: $offenders", offenders.isEmpty())
    }

    /** The guard must actually be reading the handoff, or it proves nothing. */
    @Test
    fun `the guard scans the system handoff`() {
        assertTrue(File(mainSourceRoot(), "com/ventouxlabs/gatepath/CaptivePortalActivity.kt").isFile)
    }

    /**
     * `android/app/src/main/java`, from `-Dgatepath.repo.root` (set by
     * run-jvm-tests.sh, like the other parity tests) or by walking up from the
     * working directory (Gradle runs in android/app).
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
