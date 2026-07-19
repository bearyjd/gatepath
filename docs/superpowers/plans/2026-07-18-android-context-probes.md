# Android Context-Only Diagnostics Probes Implementation Plan (PR 1 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill in the four context-only diagnostic probes (VPN, HTTP proxy, no-DNS, cellular fallback), add the `NoDnsServers` cause, name every probe's result in the UI ("All checks" list), and add a manual "Run diagnostics again" button.

**Architecture:** All new probes are pure-JVM files in `android/app/src/main/java/com/ventouxlabs/gatepath/diag/`, one concern per file, registered in `DiagnosticModule`. The engine's `DiagnosisResult` gains per-probe names (`ProbeCheck`). Platform glue (ConnectivityManager snapshot, button wiring) stays in `CaptivePortalMonitor` / `MainViewModel` / Compose UI.

**Tech Stack:** Kotlin, Jetpack Compose, Hilt, JUnit4 + kotlinx-coroutines `runBlocking` (no coroutines-test), JVM test harness `android/run-jvm-tests.sh`.

**Spec:** `docs/superpowers/specs/2026-07-18-diagnostics-expansion-design.md`. One recorded deviation: `hasValidatedCellular` is populated in `CaptivePortalMonitor.buildDiagnostics` (which already owns the `ConnectivityManager`), not in `MainViewModel` as the spec sketched — same data flow, better home. Scope note: of the three new causes, only `NoDnsServers` lands here; `PortalRedirectLoop` and `ClockSkew` land in PR 2 alongside the network probes that emit them (a cause with no emitter is dead code).

## Global Constraints

- Work on branch `feat/android-context-probes` off `main`. Never push to `main`; land via reviewed PR (repo convention).
- Do NOT add `kotlin("android")` to `android/app/build.gradle.kts` — AGP 9 hard-fails (pinned comment in that file).
- Probes must stay pure JVM: no `android.*` imports anywhere under `diag/` except `DiagnosticModule.kt`'s Hilt annotations.
- D1: recommended actions are descriptors only; never auto-apply a fix.
- D3: engine budgets are 5s total / 2s per probe; context-only probes must not touch the network.
- `DiagnosticEngine.rankOf` is the single source of truth for severity; UI must not re-rank.
- `android/run-jvm-tests.sh` compiles a **hardcoded file list** — every new production file must be added to its `MAIN_SOURCES` block (~line 126) and every new test file to its `TEST_SOURCES` block (~line 196), in the same task that creates the file. A test file not listed there silently never runs; verify the new test names appear in the run output.
- Test command (no Android SDK needed): `bash android/run-jvm-tests.sh` from the repo root. Requires JDK 21, kotlinc 2.0.x, python3. If `ANDROID_HOME` is set, also run `(cd android && ./gradlew :app:assembleDebug)` at the end; otherwise CI covers it.
- Commit format: `<type>: <description>` (feat/fix/test/docs/chore). No attribution lines.

---

### Task 1: Engine returns named per-probe checks (`ProbeCheck`)

The UI needs to show "which check said what". Today `DiagnosisResult.all` is a bare `List<DiagnosticReport>` with no probe names. Replace it with `checks: List<ProbeCheck>`.

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticEngine.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticsBundle.kt` (renderDiagnosis)
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/DiagnosticEngineTest.kt`
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/DiagnosticsBundleTest.kt`

**Interfaces:**
- Produces: `data class ProbeCheck(val probeName: String, val report: DiagnosticReport)`; `DiagnosisResult(top: DiagnosticReport, checks: List<ProbeCheck>, recommended: RecommendedAction)`. `DiagnosisResult.all` is REMOVED — later tasks use `checks`.

- [ ] **Step 1: Write the failing test**

Add to `DiagnosticEngineTest.kt`:

```kotlin
@Test
fun `checks carry the emitting probe's name in probe-list order`() = runBlocking {
    val engine = DiagnosticEngine(
        probes = listOf(
            probe("vpn", DiagnosticReport.VpnBlocking("tun0", isFullTunnel = true)),
            probe("ok", DiagnosticReport.Healthy),
        ),
    )
    val result = engine.run(noopCtx)
    assertEquals(listOf("vpn", "ok"), result.checks.map { it.probeName })
    assertTrue(result.checks[0].report is DiagnosticReport.VpnBlocking)
    assertEquals(DiagnosticReport.Healthy, result.checks[1].report)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash android/run-jvm-tests.sh`
Expected: compile FAILURE — `unresolved reference: checks`.

- [ ] **Step 3: Implement**

In `DiagnosticEngine.kt`, replace the `DiagnosisResult` declaration and the body of `run` (keep everything else — class doc, `rankOf`, `recommendedActionFor` — unchanged):

```kotlin
/** One probe's named outcome from an engine run. */
data class ProbeCheck(
    val probeName: String,
    val report: DiagnosticReport,
)

/** Result of one engine run — top finding + every probe's named outcome. */
data class DiagnosisResult(
    val top: DiagnosticReport,
    val checks: List<ProbeCheck>,
    val recommended: RecommendedAction,
)
```

```kotlin
    @OptIn(ExperimentalCoroutinesApi::class)
    suspend fun run(ctx: ProbeContext): DiagnosisResult = coroutineScope {
        val deferred: List<Deferred<DiagnosticReport>> = probes.map { probe ->
            async {
                runCatching {
                    withTimeout(perProbeBudgetMs) { probe.run(ctx) }
                }.getOrElse { ex ->
                    DiagnosticReport.Inconclusive(
                        listOf("${probe.name}: ${ex.message ?: ex.javaClass.simpleName}"),
                    )
                }
            }
        }

        val reports = withTimeoutOrNull(totalBudgetMs) { deferred.awaitAll() }
            ?: deferred.mapIndexed { i, d ->
                if (d.isCompleted) {
                    d.getCompleted()
                } else {
                    d.cancel()
                    DiagnosticReport.Inconclusive(listOf("${probes[i].name}: total budget exceeded"))
                }
            }

        val checks = probes.mapIndexed { i, probe -> ProbeCheck(probe.name, reports[i]) }
        val nonHealthy = reports.filterNot { it is DiagnosticReport.Healthy }
        val ranked = nonHealthy.sortedByDescending(::rankOf)
        val top = ranked.firstOrNull() ?: DiagnosticReport.Healthy

        DiagnosisResult(
            top = top,
            checks = checks,
            recommended = recommendedActionFor(top),
        )
    }
```

In `DiagnosticsBundle.kt`, change `renderDiagnosis`'s findings loop:

```kotlin
            append("all_findings:")
            for (check in diagnosis.checks) {
                append("\n  - ${check.probeName}: ${renderReport(check.report)}")
            }
```

- [ ] **Step 4: Fix remaining compile errors in tests**

In `DiagnosticEngineTest.kt`, replace every use of `result.all` with `result.checks` (e.g. `assertEquals(4, result.all.size)` → `assertEquals(4, result.checks.size)`). In `DiagnosticsBundleTest.kt`, update any `DiagnosisResult(...)` constructions to the new shape (wrap reports as `ProbeCheck("name", report)`) and update expected `all_findings` text to include the `name: ` prefix. Read each failing assertion and adjust the expected string to the new render format — do not weaken assertions.

- [ ] **Step 5: Run all tests**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS (all suites).

- [ ] **Step 6: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/ android/app/src/test/java/com/ventouxlabs/gatepath/diag/
git commit -m "refactor: name per-probe results in DiagnosisResult (ProbeCheck)"
```

---

### Task 2: `NoDnsServers` cause + rank + recommended action

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticReport.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticEngine.kt` (rankOf, recommendedActionFor)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/RecommendedAction.kt` (Ids)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticsBundle.kt` (renderReport)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/DiagnosisPanel.kt` (headline, intentFor, buttonLabel)
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/DiagnosticEngineTest.kt`

**Interfaces:**
- Produces: `DiagnosticReport.NoDnsServers` (data object), `RecommendedAction.Ids.RECONNECT_NETWORK = "reconnect_network"`. Rank: 85 (between DnsHijack 90 and PrivateDnsBlocking 80).

- [ ] **Step 1: Write the failing test**

Add to `DiagnosticEngineTest.kt`:

```kotlin
@Test
fun `NoDnsServers outranks PrivateDnsBlocking and recommends reconnect`() = runBlocking {
    val engine = DiagnosticEngine(
        probes = listOf(
            probe("dns", DiagnosticReport.PrivateDnsBlocking("dns.example")),
            probe("nodns", DiagnosticReport.NoDnsServers),
        ),
    )
    val result = engine.run(noopCtx)
    assertEquals(DiagnosticReport.NoDnsServers, result.top)
    assertEquals(
        RecommendedAction.Ids.RECONNECT_NETWORK,
        (result.recommended as RecommendedAction.UserAction).id,
    )
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash android/run-jvm-tests.sh`
Expected: compile FAILURE — `unresolved reference: NoDnsServers`.

- [ ] **Step 3: Implement**

`DiagnosticReport.kt` — add before `Inconclusive`:

```kotlin
    /**
     * DHCP handed the network zero DNS servers — a half-broken connect. No DNS
     * means the captive redirect can never resolve; reconnecting usually
     * completes DHCP properly.
     */
    data object NoDnsServers : DiagnosticReport
```

`RecommendedAction.kt` — add to `companion object Ids`:

```kotlin
        const val RECONNECT_NETWORK = "reconnect_network"
```

`DiagnosticEngine.kt` — add to `rankOf` (order the arms by rank, so insert after `DnsHijack`):

```kotlin
        is DiagnosticReport.NoDnsServers -> 85
```

and to `recommendedActionFor`, before the `NoActionAvailable` group:

```kotlin
        is DiagnosticReport.NoDnsServers -> RecommendedAction.UserAction(
            id = RecommendedAction.Ids.RECONNECT_NETWORK,
            instruction = "This network gave no DNS servers — the connection is half-broken. Forget or reconnect to the network in Wi-Fi settings.",
        )
```

`DiagnosticsBundle.kt` — add to `renderReport`:

```kotlin
        is DiagnosticReport.NoDnsServers ->
            "NoDnsServers"
```

`DiagnosisPanel.kt` — add to `headline`:

```kotlin
    is DiagnosticReport.NoDnsServers ->
        "The network gave no DNS servers"
```

to `intentFor`:

```kotlin
    RecommendedAction.Ids.RECONNECT_NETWORK ->
        Intent(Settings.ACTION_WIFI_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
```

to `buttonLabel`:

```kotlin
    RecommendedAction.Ids.RECONNECT_NETWORK -> "Open Wi-Fi settings"
```

- [ ] **Step 4: Run tests**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS. (The `when`s over `DiagnosticReport` are exhaustive — if the compiler flags a missed arm anywhere else, add the `NoDnsServers` arm there too rather than an `else`.)

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/ android/app/src/test/java/com/ventouxlabs/gatepath/
git commit -m "feat: add NoDnsServers diagnostic cause with reconnect action"
```

---

### Task 3: `VpnProbe`

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/VpnProbe.kt`
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/VpnProbeTest.kt`

**Interfaces:**
- Consumes: `ProbeContext.vpnInterfaces`, `ProbeContext.isTailscaleFullTunnel`.
- Produces: `class VpnProbe : DiagnosticProbe` with `name = "vpn"`, emitting `DiagnosticReport.VpnBlocking(interfaceName, isFullTunnel)` or `Healthy`.

- [ ] **Step 1: Write the failing test**

Create `VpnProbeTest.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnProbeTest {

    private fun ctx(vpnInterfaces: List<String>, tailscaleFullTunnel: Boolean = false) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = vpnInterfaces,
        isTailscaleFullTunnel = tailscaleFullTunnel,
        dnsServerCount = 1,
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `vpn interface present emits VpnBlocking with first interface`() = runBlocking {
        val report = VpnProbe().run(ctx(vpnInterfaces = listOf("tun0", "wg0")))
        assertTrue(report is DiagnosticReport.VpnBlocking)
        report as DiagnosticReport.VpnBlocking
        assertEquals("tun0", report.interfaceName)
        assertEquals(false, report.isFullTunnel)
    }

    @Test
    fun `tailscale exit node marks full tunnel`() = runBlocking {
        val report = VpnProbe().run(
            ctx(vpnInterfaces = listOf("tailscale0"), tailscaleFullTunnel = true),
        )
        report as DiagnosticReport.VpnBlocking
        assertEquals(true, report.isFullTunnel)
    }

    @Test
    fun `tailscale full tunnel without a detected interface still reports`() = runBlocking {
        val report = VpnProbe().run(ctx(vpnInterfaces = emptyList(), tailscaleFullTunnel = true))
        report as DiagnosticReport.VpnBlocking
        assertEquals("tailscale", report.interfaceName)
        assertEquals(true, report.isFullTunnel)
    }

    @Test
    fun `no vpn emits Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, VpnProbe().run(ctx(vpnInterfaces = emptyList())))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash android/run-jvm-tests.sh`
Expected: compile FAILURE — `unresolved reference: VpnProbe`.

- [ ] **Step 3: Implement**

Create `VpnProbe.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

/**
 * Reads [ProbeContext.vpnInterfaces] / [ProbeContext.isTailscaleFullTunnel]
 * and returns [DiagnosticReport.VpnBlocking] when a VPN is up while the
 * captive portal is unresolved.
 *
 * Any VPN interface is reported — even split-tunnel setups routinely install
 * DNS rules that break captive resolution, so the finding is worth surfacing;
 * [DiagnosticReport.VpnBlocking.isFullTunnel] tells the UI how certain the
 * "pause your VPN" advice is. A Tailscale exit node without a matched
 * interface name (interface enumeration can race teardown) still reports,
 * with a fallback name.
 *
 * No network call — completes synchronously inside the per-probe budget.
 */
class VpnProbe : DiagnosticProbe {
    override val name = "vpn"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        val interfaceName = ctx.vpnInterfaces.firstOrNull()
            ?: if (ctx.isTailscaleFullTunnel) "tailscale" else return DiagnosticReport.Healthy
        return DiagnosticReport.VpnBlocking(
            interfaceName = interfaceName,
            isFullTunnel = ctx.isTailscaleFullTunnel,
        )
    }
}
```

- [ ] **Step 4: Run tests**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/VpnProbe.kt android/app/src/test/java/com/ventouxlabs/gatepath/diag/VpnProbeTest.kt
git commit -m "feat: add VpnProbe emitting VpnBlocking from context"
```

---

### Task 4: `HttpProxyProbe`

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/HttpProxyProbe.kt`
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/HttpProxyProbeTest.kt`

**Interfaces:**
- Consumes: `ProbeContext.httpProxyDescription` (null = no proxy; non-null examples: `"proxy.corp:3128"`, `"PAC: http://wpad/wpad.dat"`).
- Produces: `class HttpProxyProbe : DiagnosticProbe`, `name = "http_proxy"`, emits `HttpProxyBlocking(description)` or `Healthy`.

- [ ] **Step 1: Write the failing test**

Create `HttpProxyProbeTest.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HttpProxyProbeTest {

    private fun ctx(proxy: String?) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = proxy,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `configured proxy emits HttpProxyBlocking with description`() = runBlocking {
        val report = HttpProxyProbe().run(ctx(proxy = "proxy.corp:3128"))
        assertTrue(report is DiagnosticReport.HttpProxyBlocking)
        assertEquals("proxy.corp:3128", (report as DiagnosticReport.HttpProxyBlocking).description)
    }

    @Test
    fun `no proxy emits Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, HttpProxyProbe().run(ctx(proxy = null)))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash android/run-jvm-tests.sh`
Expected: compile FAILURE — `unresolved reference: HttpProxyProbe`.

- [ ] **Step 3: Implement**

Create `HttpProxyProbe.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

/**
 * Reads [ProbeContext.httpProxyDescription] and returns
 * [DiagnosticReport.HttpProxyBlocking] when the network has a per-network
 * HTTP proxy (static or PAC) configured. Most captive gateways don't route
 * their redirect through the proxy, so sign-in silently dies.
 *
 * No network call — completes synchronously inside the per-probe budget.
 */
class HttpProxyProbe : DiagnosticProbe {
    override val name = "http_proxy"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport =
        when (val proxy = ctx.httpProxyDescription) {
            null -> DiagnosticReport.Healthy
            else -> DiagnosticReport.HttpProxyBlocking(description = proxy)
        }
}
```

- [ ] **Step 4: Run tests**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/HttpProxyProbe.kt android/app/src/test/java/com/ventouxlabs/gatepath/diag/HttpProxyProbeTest.kt
git commit -m "feat: add HttpProxyProbe emitting HttpProxyBlocking from context"
```

---

### Task 5: `NoDnsProbe`

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/NoDnsProbe.kt`
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/NoDnsProbeTest.kt`

**Interfaces:**
- Consumes: `ProbeContext.dnsServerCount`.
- Produces: `class NoDnsProbe : DiagnosticProbe`, `name = "no_dns"`, emits `DiagnosticReport.NoDnsServers` (from Task 2) or `Healthy`.

- [ ] **Step 1: Write the failing test**

Create `NoDnsProbeTest.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

class NoDnsProbeTest {

    private fun ctx(dnsServerCount: Int) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = dnsServerCount,
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `zero DNS servers emits NoDnsServers`() = runBlocking {
        assertEquals(DiagnosticReport.NoDnsServers, NoDnsProbe().run(ctx(dnsServerCount = 0)))
    }

    @Test
    fun `at least one DNS server emits Healthy`() = runBlocking {
        assertEquals(DiagnosticReport.Healthy, NoDnsProbe().run(ctx(dnsServerCount = 1)))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash android/run-jvm-tests.sh`
Expected: compile FAILURE — `unresolved reference: NoDnsProbe`.

- [ ] **Step 3: Implement**

Create `NoDnsProbe.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

/**
 * Reads [ProbeContext.dnsServerCount] and returns
 * [DiagnosticReport.NoDnsServers] when DHCP handed the network zero DNS
 * servers — a half-broken connect where the captive redirect can never
 * resolve.
 *
 * No network call — completes synchronously inside the per-probe budget.
 */
class NoDnsProbe : DiagnosticProbe {
    override val name = "no_dns"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport =
        if (ctx.dnsServerCount == 0) {
            DiagnosticReport.NoDnsServers
        } else {
            DiagnosticReport.Healthy
        }
}
```

- [ ] **Step 4: Run tests**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/NoDnsProbe.kt android/app/src/test/java/com/ventouxlabs/gatepath/diag/NoDnsProbeTest.kt
git commit -m "feat: add NoDnsProbe emitting NoDnsServers from context"
```

---

### Task 6: `hasValidatedCellular` context field + `CellularFallbackProbe`

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/ProbeContext.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/network/NetworkDiagnostics.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/network/CaptivePortalMonitor.kt` (buildDiagnostics)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/MainViewModel.kt` (runDiagnosticEngine ctx)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/MainScreen.kt` (TroubleshootingPanel row)
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/CellularFallbackProbe.kt`
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/CellularFallbackProbeTest.kt`

**Interfaces:**
- Produces: `ProbeContext.hasValidatedCellular: Boolean = false` (defaulted so existing test fixtures compile unchanged); `NetworkDiagnostics.hasValidatedCellular: Boolean`; `class CellularFallbackProbe : DiagnosticProbe`, `name = "cellular_fallback"`, emits `CellularFallback(cellularValidated = true)` or `Healthy`.

- [ ] **Step 1: Write the failing test**

Create `CellularFallbackProbeTest.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.ProbeResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CellularFallbackProbeTest {

    private fun ctx(hasValidatedCellular: Boolean) = ProbeContext(
        networkId = "test",
        isPrivateDnsActive = false,
        privateDnsServer = null,
        httpProxyDescription = null,
        vpnInterfaces = emptyList(),
        isTailscaleFullTunnel = false,
        dnsServerCount = 1,
        hasValidatedCellular = hasValidatedCellular,
        activeProbe = { ProbeResult.Validated },
    )

    @Test
    fun `validated cellular alongside captive wifi emits CellularFallback`() = runBlocking {
        val report = CellularFallbackProbe().run(ctx(hasValidatedCellular = true))
        assertTrue(report is DiagnosticReport.CellularFallback)
        assertEquals(true, (report as DiagnosticReport.CellularFallback).cellularValidated)
    }

    @Test
    fun `no validated cellular emits Healthy`() = runBlocking {
        assertEquals(
            DiagnosticReport.Healthy,
            CellularFallbackProbe().run(ctx(hasValidatedCellular = false)),
        )
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash android/run-jvm-tests.sh`
Expected: compile FAILURE — no `hasValidatedCellular` parameter / `unresolved reference: CellularFallbackProbe`.

- [ ] **Step 3: Implement the pure pieces**

`ProbeContext.kt` — add the field after `dnsServerCount` (before `activeProbe`), with a doc line and a default so existing fixtures compile:

```kotlin
    /**
     * `true` if some *other* network is cellular AND validated right now —
     * i.e. mobile data is silently carrying traffic while the user thinks
     * they're on the captive WiFi.
     */
    val hasValidatedCellular: Boolean = false,
```

Create `CellularFallbackProbe.kt`:

```kotlin
package com.ventouxlabs.gatepath.diag

/**
 * Reads [ProbeContext.hasValidatedCellular] and returns
 * [DiagnosticReport.CellularFallback] when validated cellular is up while the
 * WiFi is stuck captive — mobile data masks the captive state, so pages load
 * but the portal never appears.
 *
 * No network call — completes synchronously inside the per-probe budget.
 */
class CellularFallbackProbe : DiagnosticProbe {
    override val name = "cellular_fallback"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport =
        if (ctx.hasValidatedCellular) {
            DiagnosticReport.CellularFallback(cellularValidated = true)
        } else {
            DiagnosticReport.Healthy
        }
}
```

- [ ] **Step 4: Run tests**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS.

- [ ] **Step 5: Implement the platform glue (not covered by JVM harness — verified by Gradle/CI)**

`NetworkDiagnostics.kt` — add after `dnsServerCount`:

```kotlin
    /**
     * `true` if a different network was cellular AND validated when this
     * snapshot was taken — mobile data silently carrying traffic can mask
     * the captive WiFi state entirely.
     */
    val hasValidatedCellular: Boolean,
```

`CaptivePortalMonitor.kt` — in `buildDiagnostics`, add to the `NetworkDiagnostics(...)` construction:

```kotlin
            hasValidatedCellular = hasValidatedCellular(),
```

and add this private helper below `buildDiagnostics` (`NetworkCapabilities` is already imported for the callback):

```kotlin
    /**
     * `true` if any currently-known network is cellular AND validated.
     * `allNetworks` is deprecated in favor of callback tracking, but for a
     * one-shot diagnostic snapshot the simple enumeration is the right tool.
     */
    private fun hasValidatedCellular(): Boolean = runCatching {
        @Suppress("DEPRECATION")
        connectivityManager.allNetworks.any { net ->
            val caps = connectivityManager.getNetworkCapabilities(net) ?: return@any false
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        }
    }.getOrDefault(false)
```

`MainViewModel.kt` — in `runDiagnosticEngine`'s `ProbeContext(...)`, add:

```kotlin
                hasValidatedCellular = diagnostics.hasValidatedCellular,
```

`MainScreen.kt` — in `TroubleshootingPanel`, after the `DiagnosticRow("DNS servers", ...)` line, add:

```kotlin
            if (diagnostics.hasValidatedCellular) {
                DiagnosticRow("Cellular", "validated (may mask captive WiFi)")
            }
```

- [ ] **Step 6: Run tests again, then commit**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS. (`NetworkDiagnostics`/monitor/VM/UI aren't compiled by the JVM harness; if `ANDROID_HOME` is set also run `(cd android && ./gradlew :app:assembleDebug)`, otherwise CI verifies.)

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/ android/app/src/test/java/com/ventouxlabs/gatepath/diag/CellularFallbackProbeTest.kt
git commit -m "feat: add CellularFallbackProbe with hasValidatedCellular snapshot field"
```

---

### Task 7: Register the four new probes in `DiagnosticModule`

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticModule.kt`

**Interfaces:**
- Consumes: `VpnProbe` (Task 3), `HttpProxyProbe` (Task 4), `NoDnsProbe` (Task 5), `CellularFallbackProbe` (Task 6).

- [ ] **Step 1: Update the probe list**

In `DiagnosticModule.provideDiagnosticEngine`, replace the `probes = listOf(...)` with (order mirrors `rankOf` severity — purely cosmetic, the engine ranks results itself):

```kotlin
        probes = listOf(
            VpnProbe(),
            NoDnsProbe(),
            PrivateDnsProbe(),
            HttpProxyProbe(),
            CellularFallbackProbe(),
            HttpProbe(),
        ),
```

- [ ] **Step 2: Run tests**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS (module isn't JVM-compiled, but keeps the tree consistent; Gradle/CI verifies the Hilt wiring).

- [ ] **Step 3: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticModule.kt
git commit -m "feat: register context-only probes in the diagnostic battery"
```

---

### Task 8: Manual "Run diagnostics again" button

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/network/CaptivePortalMonitor.kt` (public snapshot wrapper)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/MainViewModel.kt` (store suspected network; `rerunDiagnostics()`)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/MainScreen.kt` (button + new parameter)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/MainActivity.kt` (both `MainScreen(` call sites)

**Interfaces:**
- Produces: `CaptivePortalMonitor.snapshotDiagnostics(network: Network, bindError: String?, fallbackError: String?): NetworkDiagnostics`; `MainViewModel.rerunDiagnostics()`; `MainScreen(..., onRunDiagnostics: () -> Unit, ...)`.

This task is Android-glue only — no JVM-testable surface. Verification is compile (Gradle/CI) + the e2e harness later.

- [ ] **Step 1: Expose a snapshot method on the monitor**

In `CaptivePortalMonitor.kt`, above the private `buildDiagnostics`, add:

```kotlin
    /**
     * Re-snapshot the environment (VPN, Private DNS, proxy, DNS count,
     * cellular) for a network we already flagged as suspected-captive. Used by
     * the manual "Run diagnostics again" path so the user sees fresh state
     * after e.g. pausing their VPN. The probe errors are carried over from the
     * original failure — this method does not re-probe.
     */
    fun snapshotDiagnostics(
        network: Network,
        bindError: String?,
        fallbackError: String?,
    ): NetworkDiagnostics = buildDiagnostics(network, bindError, fallbackError)
```

- [ ] **Step 2: Add the re-run entry point to the ViewModel**

In `MainViewModel.kt`:

Add a field next to `_latestDiagnostics`:

```kotlin
    /** Network from the most recent CaptivePortalSuspected — target for manual re-runs. */
    private var suspectedNetwork: Network? = null
```

In the `CaptivePortalSuspected` branch, before `runDiagnosticEngine(...)`:

```kotlin
                        suspectedNetwork = event.network
```

In every branch that sets `_diagnosis.value = null` (the `NetworkValidated`, `NetworkObservedNoPortal`, and `CaptiveNetworkLost` branches), also add:

```kotlin
                        suspectedNetwork = null
```

Add the public method after `runDiagnosticEngine`:

```kotlin
    /**
     * Manual re-run from the UI ("Run diagnostics again"). Re-snapshots the
     * environment for the suspected network — so a just-paused VPN or a fixed
     * proxy shows up — and re-runs the engine. The original probe errors are
     * carried over; the engine's HttpProbe independently re-tests the network
     * path. No-op if nothing is suspected or the snapshot fails (network torn
     * down mid-tap): the previous diagnosis stays on screen rather than
     * flashing to empty.
     */
    fun rerunDiagnostics() {
        val network = suspectedNetwork ?: return
        val previous = _latestDiagnostics.value
        val fresh = runCatching {
            monitor.snapshotDiagnostics(
                network = network,
                bindError = previous?.bindProbeError,
                fallbackError = previous?.fallbackProbeError,
            )
        }.getOrElse { ex ->
            Log.w(TAG, "Diagnostics re-run snapshot failed: ${ex.message}")
            return
        }
        _latestDiagnostics.value = fresh
        runDiagnosticEngine(network, fresh)
    }
```

- [ ] **Step 3: Add the button to MainScreen**

In `MainScreen.kt`, add the parameter after `onDismiss`:

```kotlin
    onRunDiagnostics: () -> Unit,
```

After the `TroubleshootingPanel(diagnostics)` block (inside its own `CaptivePending` guard, directly below), add:

```kotlin
        if (networkStatus == NetworkStatus.CaptivePending) {
            Spacer(modifier = Modifier.height(16.dp))
            Button(onClick = onRunDiagnostics) {
                Text("Run diagnostics again")
            }
        }
```

- [ ] **Step 4: Wire the call sites**

In `MainActivity.kt`, both `MainScreen(` invocations (around lines 60 and 70) get the new argument alongside `onDismiss`:

```kotlin
                                onRunDiagnostics = viewModel::rerunDiagnostics,
```

(Match the receiver name actually used at each call site — if the file uses a different variable name than `viewModel`, use that.)

- [ ] **Step 5: Verify + commit**

Run: `bash android/run-jvm-tests.sh` (guards against accidental pure-layer breakage).
Expected: PASS. If `ANDROID_HOME` is set: `(cd android && ./gradlew :app:assembleDebug)` — Expected: BUILD SUCCESSFUL.

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/
git commit -m "feat: add manual Run-diagnostics-again with fresh environment snapshot"
```

---

### Task 9: "All checks" section in DiagnosisPanel

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/DiagnosisPanel.kt`

**Interfaces:**
- Consumes: `DiagnosisResult.checks: List<ProbeCheck>` (Task 1).

Compose-only — verified by compile (Gradle/CI); ordering logic stays in the engine (already JVM-tested).

- [ ] **Step 1: Add the expandable section**

In `DiagnosisPanel.kt`, add imports:

```kotlin
import androidx.compose.material3.TextButton
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
```

Inside the `Column`, after the existing action/button block, add:

```kotlin
            var showAllChecks by remember { mutableStateOf(false) }
            TextButton(onClick = { showAllChecks = !showAllChecks }) {
                Text(
                    if (showAllChecks) "Hide all checks"
                    else "Show all checks (${diagnosis.checks.size})",
                )
            }
            if (showAllChecks) {
                // Render in engine order — rankOf already decided `top`; this
                // list is informational, not a second ranking.
                diagnosis.checks.forEach { check ->
                    Text(
                        text = "${statusGlyph(check.report)} ${check.probeName}: ${checkSummary(check.report)}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onTertiaryContainer,
                    )
                }
            }
```

Add at file bottom, next to `headline`:

```kotlin
private fun statusGlyph(report: DiagnosticReport): String = when (report) {
    is DiagnosticReport.Healthy -> "✓"
    is DiagnosticReport.Inconclusive -> "?"
    else -> "✗"
}

private fun checkSummary(report: DiagnosticReport): String = when (report) {
    is DiagnosticReport.Healthy -> "no problem found"
    is DiagnosticReport.Inconclusive ->
        report.probeErrors.joinToString("; ").ifEmpty { "inconclusive" }
    else -> headline(report)
}
```

- [ ] **Step 2: Verify + commit**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS. If `ANDROID_HOME` is set: `(cd android && ./gradlew :app:assembleDebug)` — Expected: BUILD SUCCESSFUL.

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/ui/DiagnosisPanel.kt
git commit -m "feat: show per-probe All-checks list in DiagnosisPanel"
```

---

### Task 10: Final verification + PR

- [ ] **Step 1: Full JVM suite**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS, including the four new probe test files and updated engine/bundle tests.

- [ ] **Step 2: Gradle build + unit tests (SDK-dependent)**

If `ANDROID_HOME` is set:

Run: `(cd android && ./gradlew :app:test :app:assembleDebug)`
Expected: BUILD SUCCESSFUL. If no SDK is available locally, note it in the PR body — CI (`android` workflows) covers this; check `docs/BLOCKERS.md` before treating a local SDK failure as a code bug.

- [ ] **Step 3: Push branch and open PR (review-gated; do not self-merge)**

```bash
git push -u origin feat/android-context-probes
gh pr create --title "feat(android): context-only diagnostic probes + manual re-run" --body "$(cat <<'EOF'
## Summary
- Fill in four context-only probes for already-modeled causes: VpnProbe, HttpProxyProbe, NoDnsProbe, CellularFallbackProbe (new `hasValidatedCellular` snapshot field)
- New `NoDnsServers` cause (rank 85) with a reconnect recommended action
- `DiagnosisResult.all` → named `checks: List<ProbeCheck>`; DiagnosisPanel gains an expandable "All checks" list
- Manual "Run diagnostics again" button: re-snapshots the environment (fresh VPN/proxy/DNS state) and re-runs the engine

Spec: docs/superpowers/specs/2026-07-18-diagnostics-expansion-design.md (PR 1 of 5)

## Test plan
- [ ] `bash android/run-jvm-tests.sh` green (new probe tests + updated engine/bundle tests)
- [ ] CI `:app:test` + `assembleDebug` green
- [ ] CI Android e2e green (no harness changes expected in this PR)
EOF
)"
```

Also include the spec + this plan doc in the PR (they're on the branch history via `docs/diagnostics-expansion-spec` — merge or cherry-pick that branch into `feat/android-context-probes` before pushing so the PR carries the docs).
