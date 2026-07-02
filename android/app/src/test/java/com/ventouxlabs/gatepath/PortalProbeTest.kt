package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.PortalProbe
import com.ventouxlabs.gatepath.network.ProbeResult
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
 * Integration tests for [PortalProbe] against the real mockportal Python server.
 *
 * The server is spawned as a subprocess in [setUpClass] on a free port and killed
 * in [tearDownClass]. Tests use network=null (plain JVM socket — no Android SDK needed).
 */
class PortalProbeTest {

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

        /** Reset the mock server's probe counter via POST /reset. */
        private fun resetServer() {
            val url = java.net.URL("$baseUrl/reset")
            val conn = url.openConnection() as java.net.HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = 2_000
            conn.readTimeout = 2_000
            try {
                conn.connect()
                conn.responseCode // trigger the request
            } finally {
                conn.disconnect()
            }
        }
    }

    private val probe = PortalProbe()

    // ── 302 → Portal ────────────────────────────────────────────────────────

    @Test
    fun `first probe returns Portal with redirect location`() = runBlocking {
        resetServer()
        val result = probe.probe(network = null, testUrl = "$baseUrl/generate_204")
        assertTrue("Expected Portal but got $result", result is ProbeResult.Portal)
        val portal = result as ProbeResult.Portal
        assertTrue(
            "Location should point to /portal, got ${portal.locationUrl}",
            portal.locationUrl.endsWith("/portal"),
        )
    }

    // ── 204 → Validated ─────────────────────────────────────────────────────

    @Test
    fun `probe returns Validated after PORTAL_COMPLETE_AFTER redirects`() = runBlocking {
        resetServer()
        val probeUrl = "$baseUrl/generate_204"
        // Exhaust the 3 redirect calls
        repeat(3) { probe.probe(network = null, testUrl = probeUrl) }
        // 4th call should get 204
        val result = probe.probe(network = null, testUrl = probeUrl)
        assertTrue("Expected Validated but got $result", result is ProbeResult.Validated)
    }

    // ── Error on unreachable host ────────────────────────────────────────────

    @Test
    fun `probe returns Error for unreachable host`() = runBlocking {
        val result = probe.probe(network = null, testUrl = "http://192.0.2.1:19999/generate_204")
        assertTrue("Expected Error but got $result", result is ProbeResult.Error)
    }

    // ── Injected URL is used ─────────────────────────────────────────────────

    @Test
    fun `custom testUrl is used instead of default`() = runBlocking {
        resetServer()
        // Point to /portal directly — should get 200, which is "unexpected HTTP status"
        val result = probe.probe(network = null, testUrl = "$baseUrl/portal")
        // 200 is neither 204 nor 3xx, so probe should return Error
        assertTrue("Expected Error for 200 response but got $result", result is ProbeResult.Error)
        val error = result as ProbeResult.Error
        assertTrue(
            "Error message should mention status 200, got: ${error.message}",
            error.message.contains("200"),
        )
    }

    // ── instanceFollowRedirects=false verified ───────────────────────────────

    @Test
    fun `probe does not auto-follow redirects`() = runBlocking {
        resetServer()
        // If redirects were followed, we'd get the portal HTML (200) not a Portal result
        val result = probe.probe(network = null, testUrl = "$baseUrl/generate_204")
        assertTrue(
            "Probe must not follow the 302 redirect; expected Portal, got $result",
            result is ProbeResult.Portal,
        )
    }
}
