# Android Network Diagnostics Probes Implementation Plan (PR 2 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the four network-touching diagnostic probes (`RedirectLoopProbe`, `ClockSkewProbe`, `HttpsOnlyProbe`, `DnsHijackProbe`), the two remaining causes (`PortalRedirectLoop`, `ClockSkew`), and the mockportal fixtures they test against.

**Architecture:** Probes stay pure JVM by consuming new `ProbeContext` capabilities instead of touching the platform: `probeUrl` (the monitor's debug-resolved connectivity URL), `httpFetch` (single bound-network GET, redirects not followed, backed by a new JVM-testable `network/HttpFetcher.kt`), `resolveHost` (system DNS), and `nowEpochMillis` (injectable clock). DoH resolution is `httpFetch` against Cloudflare's JSON API plus a pure parser. Glue threads the monitor's `probeUrl` into the context — also fixing the pre-existing inconsistency where `MainViewModel`'s `activeProbe` ignored the debug URL override (three-authority trap, `CLAUDE.md`).

**Tech Stack:** Kotlin, kotlinx-serialization-json (already on the JVM-harness classpath), JUnit4 + `runBlocking`, Python stdlib (mockportal), pytest.

**Spec:** `docs/superpowers/specs/2026-07-18-diagnostics-expansion-design.md` (PR 2 scope). Recorded deviations, all reviewed at plan time:
1. `ClockSkewProbe` makes its own request through `httpFetch` instead of "reusing the probe response" — `ProbeResult` carries no headers, and widening it would touch the monitor path for no gain.
2. `HttpsOnlyProbe`'s HTTPS target is the https-scheme variant of `probeUrl` (host under test), not a fixed host.
3. `DnsHijackProbe` verdict policy (spec only sketched it): system resolve fails → `Inconclusive`; DoH fails/empty → `Healthy` (DoH is expected to be blocked pre-login — not evidence of hijack); both answer → `DnsHijack` only when every system answer is private/loopback AND DoH returned ≥1 public address. Conservative by design: false negatives over false alarms.
4. Probes needing several sequential requests can exceed the D3 2s per-probe budget on slow networks; the engine already degrades that to `Inconclusive("<name>: timed out...")`. Accepted — budgets are confirmed decisions and loops on real portals are LAN-fast.

## Global Constraints

- Branch: `feat/android-network-probes`, based on `feat/android-context-probes` (PR 1, #79). Open the PR with `--base feat/android-context-probes`; retarget to `main` after #79 merges. Never push to `main`; land via reviewed PR.
- Do NOT add `kotlin("android")` to `android/app/build.gradle.kts` (AGP 9 hard-fails; pinned comment there).
- Pure JVM under `diag/`: no `android.*` imports (except DiagnosticModule.kt's Hilt annotations). Importing from `com.ventouxlabs.gatepath.network` is fine (precedent: `ProbeResult`).
- D1: recommended actions are descriptors only. D3: 5s total / 2s per-probe engine budgets — do not change them.
- `DiagnosticEngine.rankOf` is the single severity authority. New ranks: `PortalRedirectLoop` 65, `ClockSkew` 55.
- `android/run-jvm-tests.sh` compiles **hardcoded file lists** — every new production file goes into its main-sources block and every new test file into its test-sources block *in the same commit*; verify the new test names appear in the run output. Current baseline: 122 tests.
- Test command: `export PATH="$HOME/.cache/gatepath-toolchain/kotlinc/bin:$PATH"; bash android/run-jvm-tests.sh` (repo root). `PortalProbeTest`-style tests may spawn `mockportal` (python3 required). Mockportal's own tests: `python -m pytest mockportal/`.
- Mockportal is a shared fixture (desktop e2e + Android e2e + unit layers): every behavior addition must be env-gated OFF by default and leave default responses byte-identical; keep the loopback-bind safeguard untouched.
- Commit format: `<type>: <description>` (feat/fix/test/docs/chore). No attribution.

---

### Task 1: `PortalRedirectLoop` + `ClockSkew` causes, ranks, actions

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticReport.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticEngine.kt` (rankOf, recommendedActionFor)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/RecommendedAction.kt` (Ids)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticsBundle.kt` (renderReport)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/DiagnosisPanel.kt` (headline, intentFor, buttonLabel)
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/DiagnosticEngineTest.kt`

**Interfaces:**
- Produces: `DiagnosticReport.PortalRedirectLoop(val chain: List<String>)`, `DiagnosticReport.ClockSkew(val skewSeconds: Long)`, `RecommendedAction.Ids.OPEN_DATE_TIME_SETTINGS = "open_date_time_settings"`. Ranks: PortalRedirectLoop 65 (between HttpProxyBlocking 70 and SandboxedWebView 60), ClockSkew 55 (between SandboxedWebView 60 and CellularFallback 50).

- [ ] **Step 1: Write the failing test**

Add to `DiagnosticEngineTest.kt`:

```kotlin
@Test
fun `redirect loop outranks clock skew and both recommend actions`() = runBlocking {
    val engine = DiagnosticEngine(
        probes = listOf(
            probe("skew", DiagnosticReport.ClockSkew(skewSeconds = 900)),
            probe("loop", DiagnosticReport.PortalRedirectLoop(chain = listOf("http://p/a", "http://p/b", "http://p/a"))),
        ),
    )
    val result = engine.run(noopCtx)
    assertTrue(result.top is DiagnosticReport.PortalRedirectLoop)
    assertEquals(
        RecommendedAction.Ids.RECONNECT_NETWORK,
        (result.recommended as RecommendedAction.UserAction).id,
    )
}

@Test
fun `clock skew recommends opening date-time settings`() = runBlocking {
    val engine = DiagnosticEngine(
        probes = listOf(probe("skew", DiagnosticReport.ClockSkew(skewSeconds = 900))),
    )
    val result = engine.run(noopCtx)
    assertEquals(
        RecommendedAction.Ids.OPEN_DATE_TIME_SETTINGS,
        (result.recommended as RecommendedAction.UserAction).id,
    )
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.cache/gatepath-toolchain/kotlinc/bin:$PATH"; bash android/run-jvm-tests.sh`
Expected: compile FAILURE — `unresolved reference: PortalRedirectLoop`.

- [ ] **Step 3: Implement**

`DiagnosticReport.kt` — add after `NoDnsServers`:

```kotlin
    /**
     * The captive gateway's sign-in redirect chain revisits a URL it already
     * issued — the portal is looping instead of serving its page (misconfigured
     * gateway, or a stale auth cookie the portal keeps bouncing). [chain] is
     * the URLs in order, ending with the first repeat.
     */
    data class PortalRedirectLoop(
        val chain: List<String>,
    ) : DiagnosticReport

    /**
     * The device clock disagrees with the gateway's HTTP `Date` header by more
     * than the tolerance. A wrong clock breaks TLS certificate validation, so
     * HTTPS portal pages fail in ways that look like network errors.
     */
    data class ClockSkew(
        val skewSeconds: Long,
    ) : DiagnosticReport
```

`RecommendedAction.kt` — add to `companion object Ids`:

```kotlin
        const val OPEN_DATE_TIME_SETTINGS = "open_date_time_settings"
```

`DiagnosticEngine.kt` — `rankOf` arms (keep the list ordered by rank):

```kotlin
        is DiagnosticReport.PortalRedirectLoop -> 65
```
(after `HttpProxyBlocking -> 70`), and
```kotlin
        is DiagnosticReport.ClockSkew -> 55
```
(after `SandboxedWebView -> 60`).

`recommendedActionFor` — add before the `NoActionAvailable` group:

```kotlin
        is DiagnosticReport.PortalRedirectLoop -> RecommendedAction.UserAction(
            id = RecommendedAction.Ids.RECONNECT_NETWORK,
            instruction = "The sign-in page is stuck in a redirect loop (${report.chain.size} hops). Forget or reconnect to the network in Wi-Fi settings.",
        )
        is DiagnosticReport.ClockSkew -> RecommendedAction.UserAction(
            id = RecommendedAction.Ids.OPEN_DATE_TIME_SETTINGS,
            instruction = "Your clock is off by about ${report.skewSeconds / 60} minutes, which breaks secure connections to the portal. Enable automatic date & time in Settings.",
        )
```

`DiagnosticsBundle.kt` — `renderReport` arms:

```kotlin
        is DiagnosticReport.PortalRedirectLoop ->
            "PortalRedirectLoop(chain=${r.chain.joinToString(" -> ")})"
        is DiagnosticReport.ClockSkew ->
            "ClockSkew(skewSeconds=${r.skewSeconds})"
```

`DiagnosisPanel.kt` — `headline` arms:

```kotlin
    is DiagnosticReport.PortalRedirectLoop ->
        "The sign-in page is stuck in a redirect loop"
    is DiagnosticReport.ClockSkew ->
        "Your device clock is wrong"
```

`intentFor` arm:

```kotlin
    RecommendedAction.Ids.OPEN_DATE_TIME_SETTINGS ->
        Intent(Settings.ACTION_DATE_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
```

`buttonLabel` arm:

```kotlin
    RecommendedAction.Ids.OPEN_DATE_TIME_SETTINGS -> "Open Date & time settings"
```

(`statusGlyph`/`checkSummary` use `else` deliberately — no change needed.)

- [ ] **Step 4: Run tests**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS, 124 tests. If the compiler flags any other exhaustive `when` over `DiagnosticReport`, add matching arms in the same style — never `else`.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/ android/app/src/test/java/com/ventouxlabs/gatepath/
git commit -m "feat: add PortalRedirectLoop and ClockSkew diagnostic causes"
```

---

### Task 2: `HttpFetcher` + new `ProbeContext` capabilities

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/network/HttpFetcher.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/ProbeContext.kt`
- Modify: `android/run-jvm-tests.sh` (add `HttpFetcher.kt` to main sources after `BoundedReader.kt`; add `HttpFetcherTest.kt` to test sources after `PortalProbeTest.kt`)
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/HttpFetcherTest.kt`

**Interfaces:**
- Produces:
  - `data class HttpFetchResult(val statusCode: Int?, val locationHeader: String?, val dateHeaderEpochMillis: Long?, val body: String?, val error: String?)` in `HttpFetcher.kt`
  - `class HttpFetcher { suspend fun fetch(network: Network?, url: String, accept: String? = null): HttpFetchResult }` — GET, `instanceFollowRedirects = false`, 2s connect / 2s read timeouts (fits D3), parses the `Date` response header to epoch millis (null if absent/unparseable), reads at most 64 KiB of body via the existing `BoundedReader`, never throws (errors land in `error`).
  - `ProbeContext` new fields (all defaulted so existing fixtures compile unchanged), inserted after `hasValidatedCellular`, before `activeProbe`:
    - `probeUrl: String = CONNECTIVITY_CHECK_URL`
    - `httpFetch: suspend (url: String, accept: String?) -> HttpFetchResult = { _, _ -> HttpFetchResult(null, null, null, null, "httpFetch not wired") }`
    - `resolveHost: suspend (host: String) -> List<String> = { emptyList() }`
    - `nowEpochMillis: () -> Long = System::currentTimeMillis`

- [ ] **Step 1: Write the failing test**

Create `HttpFetcherTest.kt` following `PortalProbeTest`'s mockportal-subprocess pattern — read `android/app/src/test/java/com/ventouxlabs/gatepath/PortalProbeTest.kt` first and reuse its server spawn/teardown helper structure exactly (same env, same port-picking, same `@Before`/`@After` shape). Test bodies:

```kotlin
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

@Test
fun `portal page body is captured`() = runBlocking {
    val r = HttpFetcher().fetch(network = null, url = "$baseUrl/portal")
    assertEquals(200, r.statusCode)
    assertTrue(r.body!!.contains("Test Portal"))
}

@Test
fun `connection failure lands in error, never throws`() = runBlocking {
    val r = HttpFetcher().fetch(network = null, url = "http://127.0.0.1:1/nope")
    assertEquals(null, r.statusCode)
    assertTrue(r.error != null)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash android/run-jvm-tests.sh`
Expected: compile FAILURE — `unresolved reference: HttpFetcher`.

- [ ] **Step 3: Implement `HttpFetcher.kt`**

```kotlin
package com.ventouxlabs.gatepath.network

import android.net.Network
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

// Tight timeouts so multi-request diagnostic probes fit the engine's D3
// per-probe budget; PortalProbe keeps its own longer timeouts for the
// monitoring path.
private const val FETCH_CONNECT_TIMEOUT_MS = 2_000
private const val FETCH_READ_TIMEOUT_MS = 2_000

/** Cap mirrors BoundedReader usage elsewhere: DoH answers and portal pages are tiny. */
private const val MAX_BODY_BYTES = 64 * 1024

/**
 * Outcome of a single no-follow GET. Pure data so diagnostic probes can be
 * driven by fakes; [error] is non-null iff the request failed before an HTTP
 * status was obtained.
 */
data class HttpFetchResult(
    val statusCode: Int?,
    val locationHeader: String?,
    val dateHeaderEpochMillis: Long?,
    val body: String?,
    val error: String?,
)

/**
 * Single-request HTTP GET for the diagnostic battery: redirects are reported,
 * never followed; the `Date` header is surfaced for clock-skew detection; the
 * body is capped via [BoundedReader]. Like [PortalProbe], [Network] is
 * nullable so the class is JVM-testable (null = default socket / route).
 */
class HttpFetcher {

    suspend fun fetch(
        network: Network?,
        url: String,
        accept: String? = null,
    ): HttpFetchResult = withContext(Dispatchers.IO) {
        runCatching {
            val u = URL(url)
            val conn = (if (network != null) network.openConnection(u) else u.openConnection()) as HttpURLConnection
            conn.apply {
                instanceFollowRedirects = false
                connectTimeout = FETCH_CONNECT_TIMEOUT_MS
                readTimeout = FETCH_READ_TIMEOUT_MS
                requestMethod = "GET"
                if (accept != null) setRequestProperty("Accept", accept)
            }
            try {
                conn.connect()
                val code = conn.responseCode
                val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                val body = stream?.let { s ->
                    s.use { BoundedReader.readUtf8Capped(it, MAX_BODY_BYTES) }
                }
                HttpFetchResult(
                    statusCode = code,
                    locationHeader = conn.getHeaderField("Location"),
                    dateHeaderEpochMillis = parseHttpDate(conn.getHeaderField("Date")),
                    body = body,
                    error = null,
                )
            } finally {
                conn.disconnect()
            }
        }.getOrElse { ex ->
            HttpFetchResult(null, null, null, null, ex.message ?: ex.javaClass.simpleName)
        }
    }

    private fun parseHttpDate(value: String?): Long? {
        if (value == null) return null
        return runCatching {
            ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME)
                .toInstant()
                .toEpochMilli()
        }.getOrNull()
    }
}
```

**Check `BoundedReader`'s actual API first** (`android/app/src/main/java/com/ventouxlabs/gatepath/network/BoundedReader.kt`): if its capped-read function has a different name/signature than `readUtf8Capped(stream, maxBytes)`, use the real one — do not add a new overload. If `BoundedReader` cannot read from an `InputStream` at all, read up to `MAX_BODY_BYTES + 1` bytes manually with a loop and decode UTF-8 — and note the deviation in the task report.

`ProbeContext.kt` — add imports `com.ventouxlabs.gatepath.network.CONNECTIVITY_CHECK_URL`, `com.ventouxlabs.gatepath.network.HttpFetchResult`, and the four fields after `hasValidatedCellular` with KDoc lines:

```kotlin
    /** URL the monitor's own connectivity probe uses (debug builds may override — see AppModule). */
    val probeUrl: String = CONNECTIVITY_CHECK_URL,

    /**
     * Single no-follow GET over the captive network. Defaults to a stub so
     * context-only test fixtures need not wire it; network probes treat the
     * stub's error as Inconclusive-grade evidence, not a finding.
     */
    val httpFetch: suspend (url: String, accept: String?) -> HttpFetchResult =
        { _, _ -> HttpFetchResult(null, null, null, null, "httpFetch not wired") },

    /** System-resolver lookup (A/AAAA string forms); empty = resolution failed. */
    val resolveHost: suspend (host: String) -> List<String> = { emptyList() },

    /** Injectable clock for skew math in tests. */
    val nowEpochMillis: () -> Long = System::currentTimeMillis,
```

- [ ] **Step 4: Wire the harness and run**

Add to `android/run-jvm-tests.sh`: `HttpFetcher.kt` in main sources (after `BoundedReader.kt`), `HttpFetcherTest.kt` in test sources (after `PortalProbeTest.kt`). Run `bash android/run-jvm-tests.sh` — Expected: PASS, 127 tests, `HttpFetcherTest`'s three names in the output.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/ android/app/src/test/java/com/ventouxlabs/gatepath/HttpFetcherTest.kt android/run-jvm-tests.sh
git commit -m "feat: add HttpFetcher and ProbeContext network capabilities"
```

---

### Task 3: `RedirectLoopProbe`

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/RedirectLoopProbe.kt`
- Modify: `android/run-jvm-tests.sh` (main after `CellularFallbackProbe.kt`; test after `CellularFallbackProbeTest.kt`)
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/RedirectLoopProbeTest.kt`

**Interfaces:**
- Consumes: `ctx.probeUrl`, `ctx.httpFetch`.
- Produces: `class RedirectLoopProbe : DiagnosticProbe`, `name = "redirect_loop"`, emits `PortalRedirectLoop(chain)` / `Healthy` / `Inconclusive`.

- [ ] **Step 1: Write the failing test**

Create `RedirectLoopProbeTest.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RedirectLoopProbeTest {

    private fun redirect(to: String) = HttpFetchResult(302, to, null, null, null)
    private fun ok204() = HttpFetchResult(204, null, null, null, null)
    private fun page200() = HttpFetchResult(200, null, null, "<html>portal</html>", null)

    private fun ctx(responses: Map<String, HttpFetchResult>) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        probeUrl = "http://portal.test/probe",
        httpFetch = { url, _ ->
            responses[url] ?: HttpFetchResult(null, null, null, null, "unexpected url: $url")
        },
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `two-node cycle is detected with the chain ending at the repeat`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(
                mapOf(
                    "http://portal.test/probe" to redirect("http://portal.test/a"),
                    "http://portal.test/a" to redirect("http://portal.test/b"),
                    "http://portal.test/b" to redirect("http://portal.test/a"),
                ),
            ),
        )
        assertTrue(report is DiagnosticReport.PortalRedirectLoop)
        val chain = (report as DiagnosticReport.PortalRedirectLoop).chain
        assertEquals(
            listOf("http://portal.test/probe", "http://portal.test/a", "http://portal.test/b", "http://portal.test/a"),
            chain,
        )
    }

    @Test
    fun `relative Location headers are resolved against the current url`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(
                mapOf(
                    "http://portal.test/probe" to redirect("/a"),
                    "http://portal.test/a" to redirect("/a"),
                ),
            ),
        )
        assertTrue(report is DiagnosticReport.PortalRedirectLoop)
    }

    @Test
    fun `chain ending in a page is Healthy`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(
                mapOf(
                    "http://portal.test/probe" to redirect("http://portal.test/portal"),
                    "http://portal.test/portal" to page200(),
                ),
            ),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `validated 204 is Healthy`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(mapOf("http://portal.test/probe" to ok204())),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `first fetch failing is Inconclusive with the error`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(mapOf("http://portal.test/probe" to HttpFetchResult(null, null, null, null, "connect timed out"))),
        )
        assertTrue(report is DiagnosticReport.Inconclusive)
        assertTrue((report as DiagnosticReport.Inconclusive).probeErrors.single().contains("connect timed out"))
    }

    @Test
    fun `long non-repeating chain gives up as Healthy at the hop cap`() = runBlocking {
        val report = RedirectLoopProbe().run(
            ctx(
                mapOf(
                    "http://portal.test/probe" to redirect("http://portal.test/1"),
                    "http://portal.test/1" to redirect("http://portal.test/2"),
                    "http://portal.test/2" to redirect("http://portal.test/3"),
                    "http://portal.test/3" to redirect("http://portal.test/4"),
                    "http://portal.test/4" to redirect("http://portal.test/5"),
                    "http://portal.test/5" to redirect("http://portal.test/6"),
                ),
            ),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }
}
```

- [ ] **Step 2: Run to verify RED** — `bash android/run-jvm-tests.sh`; expected compile failure `unresolved reference: RedirectLoopProbe`.

- [ ] **Step 3: Implement `RedirectLoopProbe.kt`**

```kotlin
package com.ventouxlabs.gatepath.diag

import java.net.URL

/** Redirect hops to follow before concluding "long but not looping". */
private const val MAX_HOPS = 5

/**
 * Follows the captive redirect chain from [ProbeContext.probeUrl] via
 * [ProbeContext.httpFetch] (one no-follow GET per hop) and reports
 * [DiagnosticReport.PortalRedirectLoop] when a URL repeats — a looping
 * gateway leaves the user staring at a spinner with no page to sign in on.
 *
 * A chain that terminates (204, page, error mid-chain) or simply runs past
 * [MAX_HOPS] without repeating is not a loop: Healthy. Only a failure of the
 * very first fetch is Inconclusive — mid-chain errors mean the gateway is
 * serving *something*, which other probes judge better.
 */
class RedirectLoopProbe : DiagnosticProbe {
    override val name = "redirect_loop"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        val visited = mutableListOf(ctx.probeUrl)
        var current = ctx.probeUrl
        repeat(MAX_HOPS) { hop ->
            val result = ctx.httpFetch(current, null)
            if (result.error != null) {
                return if (hop == 0) {
                    DiagnosticReport.Inconclusive(listOf("redirect_loop: ${result.error}"))
                } else {
                    DiagnosticReport.Healthy
                }
            }
            val status = result.statusCode ?: return DiagnosticReport.Healthy
            if (status !in 300..399) return DiagnosticReport.Healthy
            val location = result.locationHeader ?: return DiagnosticReport.Healthy
            val next = resolve(current, location)
            visited.add(next)
            if (visited.dropLast(1).contains(next)) {
                return DiagnosticReport.PortalRedirectLoop(chain = visited.toList())
            }
            current = next
        }
        return DiagnosticReport.Healthy
    }

    private fun resolve(base: String, location: String): String =
        runCatching { URL(URL(base), location).toString() }.getOrDefault(location)
}
```

- [ ] **Step 4: Wire harness + run** — add both files to `run-jvm-tests.sh`; `bash android/run-jvm-tests.sh` — Expected: PASS, 133 tests, all six `RedirectLoopProbeTest` names present.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/RedirectLoopProbe.kt android/app/src/test/java/com/ventouxlabs/gatepath/diag/RedirectLoopProbeTest.kt android/run-jvm-tests.sh
git commit -m "feat: add RedirectLoopProbe detecting portal redirect cycles"
```

---

### Task 4: `ClockSkewProbe`

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/ClockSkewProbe.kt`
- Modify: `android/run-jvm-tests.sh` (main after `RedirectLoopProbe.kt`; test after `RedirectLoopProbeTest.kt`)
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/ClockSkewProbeTest.kt`

**Interfaces:**
- Consumes: `ctx.probeUrl`, `ctx.httpFetch`, `ctx.nowEpochMillis`.
- Produces: `class ClockSkewProbe : DiagnosticProbe`, `name = "clock_skew"`, emits `ClockSkew(skewSeconds)` when |now − Date header| > 300s, else `Healthy`. No Date header / fetch error → `Healthy` (absence of evidence, not evidence).

- [ ] **Step 1: Write the failing test**

Create `ClockSkewProbeTest.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ClockSkewProbeTest {

    private val nowMs = 1_800_000_000_000L

    private fun ctx(dateHeaderMs: Long?, error: String? = null) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        probeUrl = "http://portal.test/probe",
        httpFetch = { _, _ ->
            if (error != null) HttpFetchResult(null, null, null, null, error)
            else HttpFetchResult(302, "http://portal.test/portal", dateHeaderMs, null, null)
        },
        nowEpochMillis = { nowMs },
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `device fifteen minutes ahead of gateway reports skew`() = runBlocking {
        val report = ClockSkewProbe().run(ctx(dateHeaderMs = nowMs - 900_000))
        assertTrue(report is DiagnosticReport.ClockSkew)
        assertEquals(900L, (report as DiagnosticReport.ClockSkew).skewSeconds)
    }

    @Test
    fun `device behind gateway also reports skew`() = runBlocking {
        val report = ClockSkewProbe().run(ctx(dateHeaderMs = nowMs + 900_000))
        assertTrue(report is DiagnosticReport.ClockSkew)
        assertEquals(900L, (report as DiagnosticReport.ClockSkew).skewSeconds)
    }

    @Test
    fun `skew inside the five-minute tolerance is Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, ClockSkewProbe().run(ctx(dateHeaderMs = nowMs - 200_000)))
    }

    @Test
    fun `missing Date header is Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, ClockSkewProbe().run(ctx(dateHeaderMs = null)))
    }

    @Test
    fun `fetch error is Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, ClockSkewProbe().run(ctx(dateHeaderMs = null, error = "timeout")))
    }
}
```

- [ ] **Step 2: RED** — `bash android/run-jvm-tests.sh`; expected `unresolved reference: ClockSkewProbe`.

- [ ] **Step 3: Implement `ClockSkewProbe.kt`**

```kotlin
package com.ventouxlabs.gatepath.diag

import kotlin.math.abs

/** Tolerance before a clock difference is a finding; captive gateways are rarely this wrong. */
private const val SKEW_TOLERANCE_SECONDS = 300L

/**
 * Compares the device clock ([ProbeContext.nowEpochMillis]) against the
 * gateway's HTTP `Date` header (one [ProbeContext.httpFetch] of the probe
 * URL). A clock off by more than [SKEW_TOLERANCE_SECONDS] breaks TLS
 * certificate validation, making HTTPS portal pages fail in ways users read
 * as "the Wi-Fi is broken."
 *
 * The gateway's own clock could be the wrong one — the report says the two
 * disagree, and the recommended action (enable automatic date & time) is
 * safe either way. Missing header or failed fetch is Healthy: absence of
 * evidence, and other probes surface unreachability better.
 */
class ClockSkewProbe : DiagnosticProbe {
    override val name = "clock_skew"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        val result = ctx.httpFetch(ctx.probeUrl, null)
        val gatewayMs = result.dateHeaderEpochMillis ?: return DiagnosticReport.Healthy
        val skewSeconds = abs(ctx.nowEpochMillis() - gatewayMs) / 1000
        return if (skewSeconds > SKEW_TOLERANCE_SECONDS) {
            DiagnosticReport.ClockSkew(skewSeconds = skewSeconds)
        } else {
            DiagnosticReport.Healthy
        }
    }
}
```

- [ ] **Step 4: Wire harness + run** — Expected: PASS, 138 tests, five `ClockSkewProbeTest` names present.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/ClockSkewProbe.kt android/app/src/test/java/com/ventouxlabs/gatepath/diag/ClockSkewProbeTest.kt android/run-jvm-tests.sh
git commit -m "feat: add ClockSkewProbe comparing device clock to gateway Date header"
```

---

### Task 5: `HttpsOnlyProbe`

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/HttpsOnlyProbe.kt`
- Modify: `android/run-jvm-tests.sh` (main after `ClockSkewProbe.kt`; test after `ClockSkewProbeTest.kt`)
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/HttpsOnlyProbeTest.kt`

**Interfaces:**
- Consumes: `ctx.activeProbe` (HTTP verdict), `ctx.probeUrl`, `ctx.httpFetch` (HTTPS variant).
- Produces: `class HttpsOnlyProbe : DiagnosticProbe`, `name = "https_only"`, emits `HttpsOnlyCaptive(httpsErrorMessage)` only when HTTP validates but HTTPS fails; every other combination `Healthy`.

- [ ] **Step 1: Write the failing test**

Create `HttpsOnlyProbeTest.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HttpsOnlyProbeTest {

    private var fetchedUrl: String? = null

    private fun ctx(http: ProbeResult, httpsResult: HttpFetchResult) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        probeUrl = "http://portal.test/probe",
        httpFetch = { url, _ ->
            fetchedUrl = url
            httpsResult
        },
        activeProbe = { http },
    )

    @Test
    fun `http fine but https reset reports HttpsOnlyCaptive against the https url`() = runBlocking {
        val report = HttpsOnlyProbe().run(
            ctx(ProbeResult.Validated, HttpFetchResult(null, null, null, null, "Connection reset")),
        )
        assertTrue(report is DiagnosticReport.HttpsOnlyCaptive)
        assertEquals("Connection reset", (report as DiagnosticReport.HttpsOnlyCaptive).httpsErrorMessage)
        assertEquals("https://portal.test/probe", fetchedUrl)
    }

    @Test
    fun `http and https both working is Healthy`() = runBlocking {
        val report = HttpsOnlyProbe().run(
            ctx(ProbeResult.Validated, HttpFetchResult(204, null, null, null, null)),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `http still captive is Healthy - nothing new to report`() = runBlocking {
        val report = HttpsOnlyProbe().run(
            ctx(ProbeResult.Portal("http://portal.test/portal"), HttpFetchResult(null, null, null, null, "reset")),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `http erroring is Healthy - the http probe owns that finding`() = runBlocking {
        val report = HttpsOnlyProbe().run(
            ctx(ProbeResult.Error("EPERM"), HttpFetchResult(null, null, null, null, "reset")),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }
}
```

- [ ] **Step 2: RED** — expected `unresolved reference: HttpsOnlyProbe`.

- [ ] **Step 3: Implement `HttpsOnlyProbe.kt`**

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult

/**
 * Detects captive setups that pass cleartext HTTP but kill HTTPS (TLS
 * interception, RST-on-443): re-runs the HTTP verdict via
 * [ProbeContext.activeProbe], and only when HTTP says *validated* does it try
 * the https-scheme variant of [ProbeContext.probeUrl] via
 * [ProbeContext.httpFetch]. HTTPS failing while HTTP works is the
 * [DiagnosticReport.HttpsOnlyCaptive] signature.
 *
 * While the network is still captive (HTTP → Portal) or broken (HTTP →
 * Error), HTTPS failing tells us nothing new — those cases are Healthy here
 * and owned by other probes. This is the "Phase 4 fan-out" HttpProbe's doc
 * deferred.
 */
class HttpsOnlyProbe : DiagnosticProbe {
    override val name = "https_only"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        when (ctx.activeProbe()) {
            is ProbeResult.Portal, is ProbeResult.Error -> return DiagnosticReport.Healthy
            is ProbeResult.Validated -> Unit
        }
        val httpsUrl = ctx.probeUrl.replaceFirst("http://", "https://")
        val https = ctx.httpFetch(httpsUrl, null)
        return when {
            https.error != null -> DiagnosticReport.HttpsOnlyCaptive(httpsErrorMessage = https.error)
            else -> DiagnosticReport.Healthy
        }
    }
}
```

- [ ] **Step 4: Wire harness + run** — Expected: PASS, 142 tests, four `HttpsOnlyProbeTest` names present.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/HttpsOnlyProbe.kt android/app/src/test/java/com/ventouxlabs/gatepath/diag/HttpsOnlyProbeTest.kt android/run-jvm-tests.sh
git commit -m "feat: add HttpsOnlyProbe for https-blocking captive setups"
```

---

### Task 6: `DnsHijackProbe` + DoH JSON parsing

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DnsHijackProbe.kt`
- Modify: `android/run-jvm-tests.sh` (main after `HttpsOnlyProbe.kt`; test after `HttpsOnlyProbeTest.kt`)
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/DnsHijackProbeTest.kt`

**Interfaces:**
- Consumes: `ctx.probeUrl` (host extracted), `ctx.resolveHost`, `ctx.httpFetch` (DoH JSON).
- Produces: `class DnsHijackProbe : DiagnosticProbe`, `name = "dns_hijack"`, emits `DnsHijack(hostProbed, systemAnswer, doHAnswer)` per the verdict policy in the plan header (deviation 3); internal pure helpers `parseDohAddresses(body: String): List<String>` and `isPrivateOrLoopback(address: String): Boolean` (private, same file).

- [ ] **Step 1: Write the failing test**

Create `DnsHijackProbeTest.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.HttpFetchResult
import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DnsHijackProbeTest {

    private fun dohBody(vararg addresses: String): String {
        val answers = addresses.joinToString(",") { """{"name":"connectivitycheck.gstatic.com","type":1,"data":"$it"}""" }
        return """{"Status":0,"Answer":[$answers]}"""
    }

    private fun ctx(systemAnswers: List<String>, doh: HttpFetchResult) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        probeUrl = "http://connectivitycheck.gstatic.com/generate_204",
        httpFetch = { _, accept ->
            // The DoH request must ask for the JSON media type.
            if (accept == "application/dns-json") doh
            else HttpFetchResult(null, null, null, null, "wrong accept: $accept")
        },
        resolveHost = { systemAnswers },
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `private system answer with public doh answer is a hijack`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(
                systemAnswers = listOf("192.168.1.1"),
                doh = HttpFetchResult(200, null, null, dohBody("142.250.180.14"), null),
            ),
        )
        assertTrue(report is DiagnosticReport.DnsHijack)
        report as DiagnosticReport.DnsHijack
        assertEquals("connectivitycheck.gstatic.com", report.hostProbed)
        assertEquals("192.168.1.1", report.systemAnswer)
        assertEquals("142.250.180.14", report.doHAnswer)
    }

    @Test
    fun `matching public answers are Healthy`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(
                systemAnswers = listOf("142.250.180.14"),
                doh = HttpFetchResult(200, null, null, dohBody("142.250.180.14"), null),
            ),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `system resolution failure is Inconclusive`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(systemAnswers = emptyList(), doh = HttpFetchResult(200, null, null, dohBody("1.2.3.4"), null)),
        )
        assertTrue(report is DiagnosticReport.Inconclusive)
    }

    @Test
    fun `doh unreachable is Healthy - expected while captive`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(systemAnswers = listOf("10.0.0.1"), doh = HttpFetchResult(null, null, null, null, "timeout")),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `malformed doh json is Healthy, never a crash`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(systemAnswers = listOf("10.0.0.1"), doh = HttpFetchResult(200, null, null, "not json {", null)),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }

    @Test
    fun `public system answer is Healthy even if doh differs`() = runBlocking {
        val report = DnsHijackProbe().run(
            ctx(
                systemAnswers = listOf("8.8.8.8"),
                doh = HttpFetchResult(200, null, null, dohBody("142.250.180.14"), null),
            ),
        )
        assertEquals(DiagnosticReport.Healthy, report)
    }
}
```

- [ ] **Step 2: RED** — expected `unresolved reference: DnsHijackProbe`.

- [ ] **Step 3: Implement `DnsHijackProbe.kt`**

```kotlin
package com.ventouxlabs.gatepath.diag

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.net.URL

/** Cloudflare's DoH JSON endpoint; answer format is stable and documented. */
private const val DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"
private const val DOH_ACCEPT = "application/dns-json"

/** DNS record type 1 = A. We only compare IPv4 answers. */
private const val TYPE_A = 1

/**
 * Compares the system resolver's answer for the connectivity-check host
 * ([ProbeContext.resolveHost]) against a DNS-over-HTTPS lookup
 * ([ProbeContext.httpFetch] on Cloudflare's JSON API). A gateway that answers
 * with its own private address while the true record is public is hijacking
 * DNS beyond the probe endpoints — the aggressive-captive signature that also
 * breaks HTTPS after sign-in.
 *
 * Verdict policy (conservative — false negatives over false alarms):
 * system resolve fails → Inconclusive; DoH unreachable/unparseable → Healthy
 * (DoH being blocked pre-login is normal captivity, not hijack evidence);
 * both answer → DnsHijack only when EVERY system answer is private/loopback
 * and DoH returned at least one public address.
 */
class DnsHijackProbe : DiagnosticProbe {
    override val name = "dns_hijack"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        val host = runCatching { URL(ctx.probeUrl).host }.getOrNull()
            ?: return DiagnosticReport.Inconclusive(listOf("dns_hijack: unparseable probe url"))

        val systemAnswers = ctx.resolveHost(host)
        if (systemAnswers.isEmpty()) {
            return DiagnosticReport.Inconclusive(listOf("dns_hijack: system resolver returned no answers for $host"))
        }

        val doh = ctx.httpFetch("$DOH_ENDPOINT?name=$host&type=A", DOH_ACCEPT)
        val dohAnswers = doh.body?.let(::parseDohAddresses).orEmpty()
        val dohPublic = dohAnswers.filterNot(::isPrivateOrLoopback)
        if (doh.error != null || dohPublic.isEmpty()) return DiagnosticReport.Healthy

        val allSystemPrivate = systemAnswers.all(::isPrivateOrLoopback)
        return if (allSystemPrivate) {
            DiagnosticReport.DnsHijack(
                hostProbed = host,
                systemAnswer = systemAnswers.first(),
                doHAnswer = dohPublic.first(),
            )
        } else {
            DiagnosticReport.Healthy
        }
    }
}

/** Extracts A-record `data` fields from a DoH JSON body; empty on any parse problem. */
internal fun parseDohAddresses(body: String): List<String> = runCatching {
    Json.parseToJsonElement(body).jsonObject["Answer"]?.jsonArray.orEmpty()
        .mapNotNull { answer ->
            val obj = answer.jsonObject
            val type = obj["type"]?.jsonPrimitive?.content?.toIntOrNull()
            if (type == TYPE_A) obj["data"]?.jsonPrimitive?.content else null
        }
}.getOrDefault(emptyList())

/** RFC1918 / loopback / link-local — the address ranges captive gateways answer with. */
internal fun isPrivateOrLoopback(address: String): Boolean {
    if (address.startsWith("10.") || address.startsWith("192.168.") ||
        address.startsWith("127.") || address.startsWith("169.254.")
    ) {
        return true
    }
    if (address.startsWith("172.")) {
        val second = address.split(".").getOrNull(1)?.toIntOrNull() ?: return false
        return second in 16..31
    }
    return false
}
```

- [ ] **Step 4: Wire harness + run** — Expected: PASS, 148 tests, all six `DnsHijackProbeTest` names present.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/DnsHijackProbe.kt android/app/src/test/java/com/ventouxlabs/gatepath/diag/DnsHijackProbeTest.kt android/run-jvm-tests.sh
git commit -m "feat: add DnsHijackProbe comparing system DNS against DoH"
```

---

### Task 7: Mockportal fixtures — redirect loop + Date skew

**Files:**
- Modify: `mockportal/server.py`
- Test: `mockportal/test_server.py` (append to the existing test file — check its actual name with `ls mockportal/` and follow its fixture pattern)

**Interfaces:**
- Produces: `GET /loop-a` → 302 to `/loop-b`; `GET /loop-b` → 302 to `/loop-a` (always, stateless, recorded in `/log`). Env `PORTAL_DATE_SKEW_SECONDS` (default `0`): when non-zero, every response's `Date` header is offset by that many seconds. Default-off: with the env unset, all responses remain byte-identical (Date offset 0 = the normal header).

- [ ] **Step 1: Write the failing tests**

Append to the mockportal test file, following its existing client/fixture helpers:

```python
def test_loop_endpoints_redirect_to_each_other(server_fixture):
    status_a, headers_a, _ = get(server_fixture, "/loop-a")
    assert status_a == 302
    assert headers_a["Location"].endswith("/loop-b")
    status_b, headers_b, _ = get(server_fixture, "/loop-b")
    assert status_b == 302
    assert headers_b["Location"].endswith("/loop-a")


def test_date_skew_env_offsets_date_header(monkeypatch):
    # Build a server with a 15-minute skew and compare its Date header to now.
    import email.utils
    import time
    server, state = build_server(host="127.0.0.1", port=0, date_skew_seconds=900)
    try:
        start_in_thread(server)
        _, headers, _ = get_raw(server, "/generate_204")
        server_date = email.utils.parsedate_to_datetime(headers["Date"]).timestamp()
        assert abs(server_date - (time.time() + 900)) < 60
    finally:
        server.shutdown()
        server.server_close()


def test_no_skew_by_default(server_fixture):
    import email.utils
    import time
    _, headers, _ = get(server_fixture, "/generate_204")
    server_date = email.utils.parsedate_to_datetime(headers["Date"]).timestamp()
    assert abs(server_date - time.time()) < 60
```

Adapt helper names (`get`, `get_raw`, `server_fixture`, `start_in_thread`) to whatever the existing test file actually provides — read it first and reuse its idioms; do not invent a parallel harness. The substantive assertions (302 pair, ±60s windows, 900s offset) are the requirement.

- [ ] **Step 2: RED** — `python -m pytest mockportal/ -q`; expected failures (404 on /loop-a; no `date_skew_seconds` parameter).

- [ ] **Step 3: Implement in `server.py`**

1. Env + parameter plumbing, mirroring `PORTAL_COMPLETE_AFTER`'s pattern:

```python
PORTAL_DATE_SKEW_SECONDS = int(os.environ.get("PORTAL_DATE_SKEW_SECONDS", "0"))
```

`build_server(...)` gains `date_skew_seconds: int = PORTAL_DATE_SKEW_SECONDS`, passed to `_make_handler`.

2. In `_make_handler(state, host, port, leak_sentinel, date_skew_seconds)`'s `Handler`, override the stdlib Date source (single hook `BaseHTTPRequestHandler` uses for the automatic Date header):

```python
        def date_time_string(self, timestamp: float | None = None) -> str:
            # Skew the automatic Date header for clock-skew diagnostics tests.
            # With skew 0 (the default) this is byte-identical to the stdlib.
            base = timestamp if timestamp is not None else __import__("time").time()
            return super().date_time_string(base + date_skew_seconds)
```

(Use a module-level `import time` instead of `__import__` — shown inline here only for brevity. `time` may already be imported; check.)

3. Loop endpoints in `do_GET`, before the 404 fallthrough:

```python
            if self.path.startswith("/loop-a"):
                self._send(302, headers={"Location": f"http://{host}:{port}/loop-b"})
                return
            if self.path.startswith("/loop-b"):
                self._send(302, headers={"Location": f"http://{host}:{port}/loop-a"})
                return
```

4. Update the module docstring's behavior list with the two additions (one line each, matching its style), including that both are default-inert.

- [ ] **Step 4: GREEN + regression** — `python -m pytest mockportal/ -q` all pass; also run `bash android/run-jvm-tests.sh` (PortalProbeTest + HttpFetcherTest spawn this server — must stay green, proving default-inertness). Expected: 148 tests.

- [ ] **Step 5: Commit**

```bash
git add mockportal/
git commit -m "feat: add redirect-loop endpoints and Date-skew mode to mockportal"
```

---

### Task 8: Register probes + glue wiring

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticModule.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/network/CaptivePortalMonitor.kt` (expose `probeUrl`)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/MainViewModel.kt` (wire the new capabilities)

**Interfaces:**
- Consumes: everything from Tasks 2-6.
- Produces: `CaptivePortalMonitor.probeUrl` becomes a public `val`; `MainViewModel.runDiagnosticEngine` builds a fully-wired `ProbeContext`.

This task is Android-glue (CI-compiled) except the module file. The JVM suite is the no-damage guard.

- [ ] **Step 1: DiagnosticModule** — replace the probe list (order mirrors rank, cosmetic):

```kotlin
        probes = listOf(
            VpnProbe(),
            DnsHijackProbe(),
            NoDnsProbe(),
            PrivateDnsProbe(),
            HttpProxyProbe(),
            RedirectLoopProbe(),
            ClockSkewProbe(),
            CellularFallbackProbe(),
            HttpsOnlyProbe(),
            HttpProbe(),
        ),
```

- [ ] **Step 2: Monitor** — change the constructor parameter `private val probeUrl: String = CONNECTIVITY_CHECK_URL` to `val probeUrl: String = CONNECTIVITY_CHECK_URL` (drop `private`; comment stays).

- [ ] **Step 3: MainViewModel** — add near `private val portalProbe = PortalProbe()`:

```kotlin
    private val httpFetcher = HttpFetcher()
```

(import `com.ventouxlabs.gatepath.network.HttpFetcher`). In `runDiagnosticEngine`, extend the `ProbeContext(...)` construction and fix the activeProbe URL:

```kotlin
                hasValidatedCellular = diagnostics.hasValidatedCellular,
                probeUrl = monitor.probeUrl,
                httpFetch = { url, accept -> httpFetcher.fetch(network = null, url = url, accept = accept) },
                resolveHost = { host ->
                    runCatching {
                        java.net.InetAddress.getAllByName(host).mapNotNull { it.hostAddress }
                    }.getOrElse { emptyList() }
                },
                activeProbe = { portalProbe.probe(network = null, testUrl = monitor.probeUrl) },
```

(Replace the existing `activeProbe` line; move the `java.net.InetAddress` reference to a normal import if the file's style prefers it. The `testUrl = monitor.probeUrl` change fixes a latent bug: the diagnostic battery previously probed hardcoded gstatic even in debug builds where the monitor targets the mock — the three-authority trap. Update the `runDiagnosticEngine` KDoc's probe paragraph to mention it probes `monitor.probeUrl`.)

`httpFetch`/`activeProbe` use `network = null` (default route) for the same documented reason as the existing closure: bind already failed when the engine runs; the default-route path is the one that can have changed.

- [ ] **Step 4: Run + commit** — `bash android/run-jvm-tests.sh` (Expected: PASS, 148). If `ANDROID_HOME` is set: `(cd android && ./gradlew :app:assembleDebug)`.

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/
git commit -m "feat: register network probes and wire ProbeContext capabilities"
```

---

### Task 9: Final verification + PR

- [ ] **Step 1:** `bash android/run-jvm-tests.sh` — Expected: PASS, 148 tests including every new probe test file. `python -m pytest mockportal/ -q` — Expected: all pass.

- [ ] **Step 2:** If `ANDROID_HOME` set: `(cd android && ./gradlew :app:test :app:assembleDebug)`; otherwise note in the PR that CI is the compile gate for glue.

- [ ] **Step 3: Push and open the stacked PR (review-gated; do not self-merge):**

```bash
git push -u origin feat/android-network-probes
gh pr create --base feat/android-context-probes --title "feat(android): network diagnostic probes (redirect loop, clock skew, HTTPS-only, DNS hijack)" --body "$(cat <<'EOF'
## Summary
- Four network-touching probes: `RedirectLoopProbe`, `ClockSkewProbe`, `HttpsOnlyProbe`, `DnsHijackProbe` (system DNS vs Cloudflare DoH JSON)
- Two new causes completing the spec vocabulary: `PortalRedirectLoop` (rank 65, reconnect action), `ClockSkew` (rank 55, new `OPEN_DATE_TIME_SETTINGS` action)
- New `network/HttpFetcher.kt` (no-follow GET, Date-header parse, BoundedReader-capped body) behind new pure `ProbeContext` capabilities: `probeUrl`, `httpFetch`, `resolveHost`, `nowEpochMillis`
- Latent bug fix: the diagnostic battery's `activeProbe` now uses the monitor's debug-resolved probe URL instead of hardcoded gstatic (three-authority trap)
- Mockportal: `/loop-a`↔`/loop-b` redirect cycle + `PORTAL_DATE_SKEW_SECONDS` Date-header skew, both default-inert

**Stacked on #79** (base `feat/android-context-probes`); retarget to `main` after #79 merges. Spec: `docs/superpowers/specs/2026-07-18-diagnostics-expansion-design.md` (PR 2 of 5).

## Test plan
- [ ] `bash android/run-jvm-tests.sh` green — 148 tests (baseline 122), all new probe + HttpFetcher tests executing
- [ ] `python -m pytest mockportal/` green; default responses byte-identical (existing e2e layers unaffected)
- [ ] CI `:app:test` + `assembleDebug` green (glue compile gate)
- [ ] CI Android e2e green (mockportal additions are default-inert)
EOF
)"
```

Include this plan doc in the branch (commit it before Task 1's commit or alongside Task 9).
