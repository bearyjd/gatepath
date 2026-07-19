package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.HttpFetcher
import kotlinx.coroutines.runBlocking
import org.junit.AfterClass
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.ServerSocket

/**
 * Integration tests for [HttpFetcher] against the real mockportal Python server.
 *
 * The server is spawned as a subprocess in [setUpClass] on a free port and killed
 * in [tearDownClass]. Tests use network=null (plain JVM socket — no Android SDK needed).
 */
class HttpFetcherTest {

    companion object {
        private var serverProcess: Process? = null
        private var serverPort: Int = 0
        private var baseUrl: String = ""

        @JvmStatic
        @BeforeClass
        fun setUpClass() {
            serverPort = findFreePort()
            baseUrl = "http://127.0.0.1:$serverPort"

            val repoRoot = findRepoRoot()
            serverProcess = ProcessBuilder(
                "python3", "-m", "mockportal.server",
            ).apply {
                environment()["PORTAL_HOST"] = "127.0.0.1"
                environment()["PORTAL_PORT"] = serverPort.toString()
                environment()["PORTAL_COMPLETE_AFTER"] = "3"
                directory(repoRoot)
                redirectErrorStream(true)
            }.start()

            // Wait for server to be ready (read first output line or timeout)
            val reader = BufferedReader(InputStreamReader(serverProcess!!.inputStream))
            val deadline = System.currentTimeMillis() + 5_000
            while (System.currentTimeMillis() < deadline) {
                if (reader.ready()) {
                    reader.readLine() // consume "mockportal listening on ..." line
                    break
                }
                Thread.sleep(100)
            }
            // Give the server an extra moment to fully bind
            Thread.sleep(300)
        }

        @JvmStatic
        @AfterClass
        fun tearDownClass() {
            serverProcess?.destroyForcibly()
            serverProcess = null
        }

        private fun findFreePort(): Int {
            ServerSocket(0).use { return it.localPort }
        }

        private fun findRepoRoot(): java.io.File {
            // Walk up from the test class location to find the repo root (contains mockportal/)
            var dir = java.io.File(System.getProperty("user.dir") ?: ".")
            repeat(5) {
                if (java.io.File(dir, "mockportal").exists()) return dir
                dir = dir.parentFile ?: return dir
            }
            return dir
        }
    }

    // ── redirect reported, not followed ─────────────────────────────────────

    @Test
    fun `redirect is reported, not followed, with location and date`() = runBlocking {
        // mockportal /generate_204 starts captive: 302 + Location + automatic Date header
        val r = HttpFetcher().fetch(network = null, url = "$baseUrl/generate_204")
        assertEquals(302, r.statusCode)
        assertTrue(r.locationHeader!!.endsWith("/portal"))
        assertTrue(r.dateHeaderEpochMillis != null)
        // sanity: server clock ≈ test clock (same machine)
        assertTrue(kotlin.math.abs(r.dateHeaderEpochMillis!! - System.currentTimeMillis()) < 60_000)
        assertEquals(null, r.error)
    }

    // ── body capture ─────────────────────────────────────────────────────────

    @Test
    fun `portal page body is captured`() = runBlocking {
        val r = HttpFetcher().fetch(network = null, url = "$baseUrl/portal")
        assertEquals(200, r.statusCode)
        assertTrue(r.body!!.contains("Test Portal"))
    }

    // ── connection failure never throws ──────────────────────────────────────

    @Test
    fun `connection failure lands in error, never throws`() = runBlocking {
        val r = HttpFetcher().fetch(network = null, url = "http://127.0.0.1:1/nope")
        assertEquals(null, r.statusCode)
        assertTrue(r.error != null)
    }
}
