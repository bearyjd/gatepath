package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.PortalProbe
import com.ventouxlabs.gatepath.network.PortalProbeCapture
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
        // Point at an endpoint the default URL would never reach. /log returns
        // 200 with a JSON body and no redirect hint, so a Portal result naming
        // exactly this URL proves the injected URL was the one fetched.
        val logUrl = "$baseUrl/log"
        val result = probe.probe(network = null, testUrl = logUrl)
        assertTrue("Expected Portal but got $result", result is ProbeResult.Portal)
        assertEquals(logUrl, (result as ProbeResult.Portal).locationUrl)
    }

    // ── 200-style intercepts → Portal ────────────────────────────────────────
    //
    // Regression coverage for the field bug: gateways that answer the
    // connectivity check with 200 instead of a 3xx were classified as Error,
    // so CaptivePortalMonitor emitted CaptivePortalSuspected and the sign-in
    // WebView never opened. See PortalProbe's KDoc.

    @Test
    fun `200 with login page in place is a Portal at the probe url`() = runBlocking {
        resetServer()
        val probeUrl = "$baseUrl/intercept-200"
        val result = probe.probe(network = null, testUrl = probeUrl)
        assertTrue("Expected Portal but got $result", result is ProbeResult.Portal)
        // No Refresh header and no meta-refresh, so the probe URL itself is the
        // portal — the gateway is already serving the login page there.
        assertEquals(probeUrl, (result as ProbeResult.Portal).locationUrl)
    }

    @Test
    fun `200 with Refresh header points at the header target`() = runBlocking {
        resetServer()
        val result = probe.probe(network = null, testUrl = "$baseUrl/intercept-refresh-header")
        assertTrue("Expected Portal but got $result", result is ProbeResult.Portal)
        val portal = result as ProbeResult.Portal
        assertTrue(
            "Should follow the Refresh header to /portal, got ${portal.locationUrl}",
            portal.locationUrl.endsWith("/portal"),
        )
    }

    @Test
    fun `200 with meta refresh points at the meta target`() = runBlocking {
        resetServer()
        val result = probe.probe(network = null, testUrl = "$baseUrl/intercept-meta")
        assertTrue("Expected Portal but got $result", result is ProbeResult.Portal)
        val portal = result as ProbeResult.Portal
        assertTrue(
            "Should follow the meta-refresh to /portal, got ${portal.locationUrl}",
            portal.locationUrl.endsWith("/portal"),
        )
    }

    @Test
    fun `200 with scripted location points at the script target`() = runBlocking {
        resetServer()
        // The variant actually observed in the field: no Refresh header, no
        // meta-refresh, just top.location.href in an inline script.
        val result = probe.probe(network = null, testUrl = "$baseUrl/intercept-script")
        assertTrue("Expected Portal but got $result", result is ProbeResult.Portal)
        val portal = result as ProbeResult.Portal
        assertTrue(
            "Should recover /portal from the script, got ${portal.locationUrl}",
            portal.locationUrl.endsWith("/portal"),
        )
    }

    // ── Non-200, non-204, non-3xx is still an Error ─────────────────────────

    @Test
    fun `404 is still an Error`() = runBlocking {
        resetServer()
        val result = probe.probe(network = null, testUrl = "$baseUrl/no-such-endpoint")
        assertTrue("Expected Error but got $result", result is ProbeResult.Error)
        assertTrue(
            "Error should mention 404, got: ${(result as ProbeResult.Error).message}",
            result.message.contains("404"),
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

    // ── The capture rides along with a Portal result ────────────────────────

    /**
     * The normaliser and the bundle renderer are each tested in isolation.
     * These assert the join: that a real probe against the mock portal actually
     * fills the capture in. Without them, a null capture or a field wired to
     * the wrong value passes every other test in the suite, and the only
     * symptom is a diagnostics bundle that quietly says
     * "(no intercepted response captured)".
     */
    @Test
    fun `a redirect intercept carries a LOCATION_HEADER capture`() = runBlocking {
        resetServer()
        val result = probe.probe(network = null, testUrl = "$baseUrl/generate_204")

        assertTrue("Expected Portal but got $result", result is ProbeResult.Portal)
        val capture = requireNotNull((result as ProbeResult.Portal).capture) {
            "a 302 intercept must carry a capture, got null"
        }
        assertEquals(PortalProbeCapture.RedirectSignal.LOCATION_HEADER, capture.redirectSignal)
        assertTrue(
            "Expected a 3xx status, got ${capture.httpStatus}",
            capture.httpStatus in 300..399,
        )
    }

    @Test
    fun `a 200 intercept carries a capture with a normalised media type`() = runBlocking {
        resetServer()
        val result = probe.probe(network = null, testUrl = "$baseUrl/log")

        assertTrue("Expected Portal but got $result", result is ProbeResult.Portal)
        val capture = requireNotNull((result as ProbeResult.Portal).capture) {
            "a 200 intercept must carry a capture, got null"
        }
        assertEquals(200, capture.httpStatus)
        // Whatever the gateway sent, the capture may only hold an allowlisted
        // media type or the stand-in — never raw header text.
        val mediaType = capture.contentType
        assertTrue(
            "content_type should be normalised, got $mediaType",
            mediaType == null ||
                mediaType == PortalProbeCapture.OTHER_MEDIA_TYPE ||
                mediaType.matches(Regex("[a-z0-9.+-]+/[a-z0-9.+-]+")),
        )
    }
}
