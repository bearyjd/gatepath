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
     * in the system handoff is that decision, so no code path may send it.
     * Comment lines are skipped so the KDoc can still name the API.
     */
    @Test
    fun `the system handoff never calls ignoreNetwork`() {
        val source = findMainSource("com/ventouxlabs/gatepath/CaptivePortalActivity.kt")
        val calls = source.readLines()
            .map { it.substringBefore("//").trim() }
            .filterNot { it.startsWith("*") || it.startsWith("/*") }
            .filter { Regex("""\bignoreNetwork\s*\(""").containsMatchIn(it) }
        assertTrue("CaptivePortalActivity calls ignoreNetwork(): $calls", calls.isEmpty())
    }

    /** Finds a main source file from Gradle's (android/app) or the JVM runner's working directory. */
    private fun findMainSource(relative: String): File {
        val candidates = listOf(
            "src/main/java/$relative",
            "app/src/main/java/$relative",
            "android/app/src/main/java/$relative",
        )
        var dir: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        while (dir != null) {
            candidates.map { File(dir, it) }.firstOrNull { it.isFile }?.let { return it }
            dir = dir.parentFile
        }
        throw AssertionError("could not find $relative from ${System.getProperty("user.dir")}")
    }
}
