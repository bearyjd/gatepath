# Confinement State and Incident Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Android app truthful about whether it is confined to the captive Wi-Fi, offer in-app sign-in only when it is, and produce an evidence record for every captive incident; bump the audit schema to v2 on both platforms.

**Architecture:** A pure-Kotlin `ConfinementState` is classified from the monitor's probe results; the ViewModel opens a session only from `Confined` and always publishes an `IncidentEvidence`. The UI is one state card. A separate debug-only test-VPN app makes the emulator harness exercise the production configuration (a VPN Gatepath does not own).

**Tech Stack:** Kotlin 2.0 / Jetpack Compose / Hilt (Android), kotlinx.serialization, JUnit 4 via `run-jvm-tests.sh` and Gradle; Python 3.11 stdlib + pytest (desktop, harness); GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-19-confinement-state-design.md`

## Global Constraints

- Pure-Kotlin files must have no `android.*` imports and must be added to `MAIN_SOURCES` / `TEST_SOURCES` in `android/run-jvm-tests.sh` (explicit lists, not globs).
- Test compile keeps `-Xfriend-paths` (already in the runner). Do not remove it.
- Never add `kotlin("android")` plugin (AGP 9 built-in Kotlin).
- Nothing gateway-authored leaves the device in the evidence record: enums, booleans, numbers, dates, SHA-256 fingerprints only (PR #152 rule).
- Audit `schema_version` becomes `2`; v1 lines already on devices must still be readable.
- Commit messages: `<type>: <description>`; no attribution lines. Work on branch `feat/confinement-state` cut from `main`; never push to `main`.
- Every emulator assertion is a host-side check over pulled artefacts in `driver/assertions.py`, never a scenario step's own `ok`.
- Verified platform facts the code must not contradict (AOSP `main`, 2026-09-18): explicit network selection by a UID under a secure VPN fails with `EPERM` in netd `checkUserNetworkAccess` unless the UID holds socket-protect rights (VPN owner or system); private DNS bypass needs system permissions; the captive delegate-UID VPN bypass needs `MAINLINE_NETWORK_STACK`.

## Local verification commands

```bash
# JVM subset (no SDK): from repo root
bash android/run-jvm-tests.sh
# Desktop
cd desktop && python -m pytest tests/ -q
# Harness unit tests
cd tests/e2e-android && python -m pytest driver scenario -q
# Android compile (needs ANDROID_HOME) — CI runs it on every PR
cd android && ./gradlew :app:assembleDebug :app:testDebugUnitTest
```

## File structure

Create:
- `android/app/src/main/java/com/ventouxlabs/gatepath/network/ProbePath.kt` — enum of which route a probe took.
- `android/app/src/main/java/com/ventouxlabs/gatepath/network/ConfinementState.kt` — sealed state + `classify()`.
- `android/app/src/main/java/com/ventouxlabs/gatepath/network/VpnKind.kt` — enum + `VpnKind.fromInterfaces()`.
- `android/app/src/main/java/com/ventouxlabs/gatepath/ui/ConfinementStateText.kt` — sentence + action table.
- `android/app/src/main/java/com/ventouxlabs/gatepath/diag/CertSummary.kt` — privacy-safe certificate summary.
- `android/app/src/main/java/com/ventouxlabs/gatepath/diag/IncidentEvidence.kt` — evidence record.
- `android/app/src/main/java/com/ventouxlabs/gatepath/ui/ConfinementCard.kt` — the status card composable.
- `android/app/src/main/java/com/ventouxlabs/gatepath/ui/VpnAppLauncher.kt` — resolves the VPN app launch intent.
- `android/testvpn/` — separate debug-only VPN app (moved from `app/src/debug/.../testvpn/`).
- Tests: `ConfinementStateTest.kt`, `VpnKindTest.kt`, `ConfinementStateTextTest.kt`, `CertSummaryTest.kt`, `IncidentEvidenceTest.kt`.

Modify:
- `network/CaptivePortalMonitor.kt` — emit one `CaptiveIncident` event with classification inputs.
- `MainViewModel.kt` — classify, gate session on `Confined`, publish evidence, debug state file.
- `ui/MainScreen.kt` — replace troubleshooting panel with `ConfinementCard`.
- `MainActivity.kt`, `CaptivePortalActivity.kt`, `ui/GatepathWebView.kt`, `ui/PortalScreen.kt`.
- `audit/AuditEntry.kt`, `audit/AuditLog.kt`, `diag/DiagnosticsBundle.kt`, `share/DiagnosticsSharer.kt`.
- `docs/audit_log_schema.json`, `docs/AUDIT_LOG_SCHEMA.md`, `desktop/gatepath/audit_log.py`, `desktop/gatepath/portal_session.py`, `desktop/gatepath/window.py`.
- `tests/e2e-android/scenario/run-scenario.py`, `driver/assertions.py`, `.github/workflows/android-e2e.yml`.
- Docs: `SECURITY_MODEL.md`, `RATIONALE.md`, `README.md`, `TESTING_ANDROID.md`, `ROADMAP.md`, `CHANGELOG.md`, `tests/e2e-android/HARNESS_NOTES.md`.

Delete:
- `network/NetworkDiagnostics.kt` comments about "restricted" (file stays; see Task 8).
- `android/app/src/debug/java/com/ventouxlabs/gatepath/testvpn/` (moved in Task 13).

---

### Task 1: Amend the spec with the verified EPERM mechanism

The spec was written before the netd source was checked. Three statements in it are wrong and would mislead every later task.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-19-confinement-state-design.md`

**Interfaces:**
- Produces: the corrected state-input table every later task implements.

- [ ] **Step 1: Replace the §1 item 1 text**

Replace the paragraph beginning "**A secure VPN overrides explicit network selection.**" with:

```markdown
1. **A secure VPN forbids explicit network selection.** netd's
   `NetworkController::checkUserNetworkAccessLocked` returns `-EPERM` when a
   secure (non-`allowBypass`) VPN applies to the calling UID and that UID
   cannot protect sockets (`isProtectableLocked`: only the VPN's owner UID or
   system UIDs). `FwmarkServer` runs this check on every `SELECT_NETWORK`, so
   `bindProcessToNetwork(wifi)` makes every `connect()` fail with
   `EPERM (Operation not permitted)`. This is the EPERM the repo has been
   attributing to "restricted captive networks". It applies to Tailscale in
   tailnet-only mode as well as exit-node mode, to TorGuard, and to WireGuard,
   none of which call `allowBypass()`. Under always-on lockdown with the tunnel
   down, `Vpn.setVpnForcedLocked` installs a PROHIBIT rule instead and the
   error is `EACCES`.
```

- [ ] **Step 2: Replace the §3 state-input rows**

Replace the `Tunnelled` and `Blocked` rows and the paragraph starting "`vpnKind` is" with:

```markdown
| `Tunnelled(vpnKind)` | Bound probe error message contains `EPERM` | "Your VPN is carrying Gatepath's traffic. Exclude Gatepath in {VPN app} to sign in here, or use the system notification." | Open VPN app |
| `Blocked(vpnKind)` | Bound probe error message contains `EACCES` | "Your VPN's kill switch is blocking Gatepath. Exclude Gatepath in {VPN app} or use the system notification." | Open VPN app |

`vpnKind` is `TAILSCALE`, `TORGUARD`, `OTHER` or `NONE`, derived from interface
names by `VpnKind.fromInterfaces`. Tailscale tailnet-only mode is a secure VPN
and produces `Tunnelled` exactly like an exit node; the only path to
`Confined` under any of these clients is excluding Gatepath in the client's
app split-tunnelling. A bound probe returning 204 means the Wi-Fi itself is
validated, which is not an incident.
```

- [ ] **Step 3: Replace §8 emulator bullet**

Replace the "**Emulator (`tests/e2e-android/`).**" bullet with:

```markdown
- **Emulator (`tests/e2e-android/`).** The current in-package test VPN proves
  the bind escapes a VPN only because Gatepath *owns* that VPN (protect
  rights). A separate debug-only APK, `android/testvpn/`, becomes the VPN
  owner so the harness can run three modes: `owner` (today's leak oracle,
  unchanged semantics), `covering` (third-party VPN covers Gatepath → assert
  `TUNNELLED`, no `/portal` hit from an Android UA, no `portal_completed`),
  `excluding` (Gatepath in the VPN's disallowed list → assert `CONFINED`,
  sign-in via the monitor path, audit entry with `confinement: confined`).
  The app writes `files/confinement-state.txt` in debug builds so the state is
  a pulled artefact. Assertions live in `driver/assertions.py`.
```

Also append to §10 Risks:

```markdown
- ROADMAP P0.1's "proven" no-leak result was obtained with Gatepath as the VPN
  owner. It remains a valid proof of the binding mechanism but not of the
  production configuration; the `covering` mode closes that gap.
```

- [ ] **Step 4: Commit**

```bash
git checkout -b feat/confinement-state main
git add docs/superpowers/specs/2026-09-19-confinement-state-design.md
git commit -m "docs: correct the spec's EPERM mechanism and emulator plan"
```

---

### Task 2: `VpnKind`, `ProbePath`, `ConfinementState` and `classify()`

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/network/VpnKind.kt`
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/network/ProbePath.kt`
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/network/ConfinementState.kt`
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/VpnKindTest.kt`
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/ConfinementStateTest.kt`
- Modify: `android/run-jvm-tests.sh` (add the three main files and two test files)

**Interfaces:**
- Consumes: `ProbeResult` (`network/PortalProbe.kt`), `PortalProbeCapture`.
- Produces:
  - `enum class VpnKind { TAILSCALE, TORGUARD, OTHER, NONE }` with `companion fun fromInterfaces(interfaces: List<String>): VpnKind`.
  - `enum class ProbePath { BOUND_WIFI, DEFAULT_ROUTE, UNKNOWN }`.
  - `sealed interface ConfinementState` with `Confined(portalUrl: String, capture: PortalProbeCapture?)`, `Tunnelled(vpnKind: VpnKind)`, `Blocked(vpnKind: VpnKind)`, `DnsStrict(portalHost: String)`, `Unknown(bindError: String?, fallbackError: String?)`, and `val schemaName: String`.
  - `data class ClassificationInputs(bound: ProbeResult, fallback: ProbeResult?, vpnInterfaces: List<String>, privateDnsStrict: Boolean, portalHostResolvedOnWifi: Boolean?)`.
  - `fun classify(inputs: ClassificationInputs): ConfinementState` (top-level in `ConfinementState.kt`).

- [ ] **Step 1: Write the failing `VpnKindTest`**

```kotlin
package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.VpnKind
import org.junit.Assert.assertEquals
import org.junit.Test

class VpnKindTest {
    @Test
    fun `tailscale interface wins over other names`() {
        assertEquals(VpnKind.TAILSCALE, VpnKind.fromInterfaces(listOf("tun0 (unknown)", "tailscale0 (split_tunnel)")))
    }

    @Test
    fun `torguard interface is recognised`() {
        assertEquals(VpnKind.TORGUARD, VpnKind.fromInterfaces(listOf("torguard0 (unknown)")))
    }

    @Test
    fun `any other vpn interface is OTHER`() {
        assertEquals(VpnKind.OTHER, VpnKind.fromInterfaces(listOf("wg0 (unknown)")))
        assertEquals(VpnKind.OTHER, VpnKind.fromInterfaces(listOf("tun0 (unknown)")))
    }

    @Test
    fun `no interfaces is NONE`() {
        assertEquals(VpnKind.NONE, VpnKind.fromInterfaces(emptyList()))
    }

    @Test
    fun `matching is case-insensitive and reads the descriptor prefix only`() {
        assertEquals(VpnKind.TAILSCALE, VpnKind.fromInterfaces(listOf("Tailscale0 (full_tunnel)")))
    }
}
```

- [ ] **Step 2: Write the failing `ConfinementStateTest`**

```kotlin
package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.ClassificationInputs
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.PortalProbeCapture
import com.ventouxlabs.gatepath.network.ProbeResult
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.network.classify
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfinementStateTest {

    private val capture = PortalProbeCapture.of(302, "text/html", PortalProbeCapture.RedirectSignal.LOCATION_HEADER)
    private val portal = ProbeResult.Portal("http://10.0.0.1/login", capture)
    private val hostPortal = ProbeResult.Portal("https://n143.network-auth.com/splash", capture)
    private val eperm = ProbeResult.Error("Failed to connect to /10.0.0.1:80: connect failed: EPERM (Operation not permitted)")
    private val eacces = ProbeResult.Error("connect failed: EACCES (Permission denied)")
    private val timeout = ProbeResult.Error("timeout")

    private fun inputs(
        bound: ProbeResult,
        fallback: ProbeResult? = null,
        vpn: List<String> = emptyList(),
        strict: Boolean = false,
        resolved: Boolean? = null,
    ) = ClassificationInputs(bound, fallback, vpn, strict, resolved)

    @Test
    fun `bound portal with ip literal is Confined and carries the url`() {
        val s = classify(inputs(portal))
        assertTrue(s is ConfinementState.Confined)
        assertEquals("http://10.0.0.1/login", (s as ConfinementState.Confined).portalUrl)
        assertEquals(capture, s.capture)
    }

    @Test
    fun `bound portal with hostname that resolved on wifi is Confined`() {
        assertTrue(classify(inputs(hostPortal, resolved = true, strict = true)) is ConfinementState.Confined)
    }

    @Test
    fun `hostname portal that fails to resolve under strict private dns is DnsStrict`() {
        val s = classify(inputs(hostPortal, resolved = false, strict = true))
        assertEquals(ConfinementState.DnsStrict("n143.network-auth.com"), s)
    }

    @Test
    fun `hostname portal that fails to resolve without strict dns is Confined not DnsStrict`() {
        // The WebView will show HOST_LOOKUP_FAILED with its own copy; DnsStrict must not fire without the cause.
        assertTrue(classify(inputs(hostPortal, resolved = false, strict = false)) is ConfinementState.Confined)
    }

    @Test
    fun `EPERM on the bound probe is Tunnelled with the vpn kind`() {
        assertEquals(ConfinementState.Tunnelled(VpnKind.TAILSCALE), classify(inputs(eperm, vpn = listOf("tailscale0 (split_tunnel)"))))
        assertEquals(ConfinementState.Tunnelled(VpnKind.NONE), classify(inputs(eperm)))
    }

    @Test
    fun `EACCES on the bound probe is Blocked`() {
        assertEquals(ConfinementState.Blocked(VpnKind.TORGUARD), classify(inputs(eacces, vpn = listOf("torguard0 (unknown)"))))
    }

    @Test
    fun `any other bound error is Unknown and keeps both error strings`() {
        val s = classify(inputs(timeout, fallback = ProbeResult.Error("unreachable")))
        assertEquals(ConfinementState.Unknown("timeout", "unreachable"), s)
    }

    @Test
    fun `bound 204 is Unknown because a validated wifi is not an incident`() {
        val s = classify(inputs(ProbeResult.Validated))
        assertTrue(s is ConfinementState.Unknown)
    }

    @Test
    fun `every state has a distinct schema name`() {
        val names = listOf(
            ConfinementState.Confined("u", null), ConfinementState.Tunnelled(VpnKind.NONE),
            ConfinementState.Blocked(VpnKind.NONE), ConfinementState.DnsStrict("h"),
            ConfinementState.Unknown(null, null),
        ).map { it.schemaName }
        assertEquals(listOf("confined", "tunnelled", "blocked", "dns_strict", "unknown"), names)
        assertEquals(names.size, names.toSet().size)
    }
}
```

- [ ] **Step 3: Add files to the runner and run tests to see them fail**

In `android/run-jvm-tests.sh` add to `MAIN_SOURCES` (after the `VpnHeuristics.kt` line):

```bash
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/VpnKind.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/ProbePath.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/ConfinementState.kt"
```

and to `TEST_SOURCES` (after the `VpnHeuristicsTest.kt` line):

```bash
    "$SRC_TEST/com/ventouxlabs/gatepath/VpnKindTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/ConfinementStateTest.kt"
```

Run: `bash android/run-jvm-tests.sh`
Expected: compile failure, "unresolved reference: VpnKind".

- [ ] **Step 4: Implement the three files**

`VpnKind.kt`:

```kotlin
package com.ventouxlabs.gatepath.network

/**
 * Which VPN client covers this process, coarse enough to pick a sentence and a
 * launch intent. Derived from [VpnDetector] interface descriptors
 * ("<iface> (<mode>)"). Pure Kotlin so the classifier is JVM-testable.
 */
enum class VpnKind {
    TAILSCALE, TORGUARD, OTHER, NONE;

    companion object {
        fun fromInterfaces(interfaces: List<String>): VpnKind {
            val names = interfaces.map { it.substringBefore(' ').lowercase() }
            return when {
                names.any { it.startsWith("tailscale") } -> TAILSCALE
                names.any { it.startsWith("torguard") } -> TORGUARD
                names.isNotEmpty() -> OTHER
                else -> NONE
            }
        }
    }
}
```

`ProbePath.kt`:

```kotlin
package com.ventouxlabs.gatepath.network

/** Which route a probe request travelled. Exported into the evidence record. */
enum class ProbePath { BOUND_WIFI, DEFAULT_ROUTE, UNKNOWN }
```

`ConfinementState.kt`:

```kotlin
package com.ventouxlabs.gatepath.network

import java.net.URI

/**
 * Is Gatepath's traffic confined to the captive Wi-Fi right now?
 *
 * Classified once per captive incident from the monitor's probe results.
 * In-app sign-in is offered only from [Confined]. Pure Kotlin: the decision
 * is the security-relevant part of the app, so it runs under the no-SDK
 * JVM suite.
 *
 * Why EPERM means "tunnelled": netd refuses explicit network selection for a
 * UID under a secure (non-bypassable) VPN unless the UID can protect sockets
 * (`NetworkController::checkUserNetworkAccessLocked`). Every VPN client we
 * care about is secure, so `bindProcessToNetwork(wifi)` fails with EPERM on
 * every connect while the VPN covers us. EACCES is the lockdown PROHIBIT rule.
 */
sealed interface ConfinementState {
    val schemaName: String

    data class Confined(val portalUrl: String, val capture: PortalProbeCapture?) : ConfinementState {
        override val schemaName get() = "confined"
    }

    data class Tunnelled(val vpnKind: VpnKind) : ConfinementState {
        override val schemaName get() = "tunnelled"
    }

    data class Blocked(val vpnKind: VpnKind) : ConfinementState {
        override val schemaName get() = "blocked"
    }

    data class DnsStrict(val portalHost: String) : ConfinementState {
        override val schemaName get() = "dns_strict"
    }

    data class Unknown(val bindError: String?, val fallbackError: String?) : ConfinementState {
        override val schemaName get() = "unknown"
    }
}

/** Everything [classify] needs; the monitor collects it, the ViewModel decides. */
data class ClassificationInputs(
    val bound: ProbeResult,
    val fallback: ProbeResult?,
    val vpnInterfaces: List<String>,
    val privateDnsStrict: Boolean,
    /** null when the portal URL has no hostname or no lookup was attempted. */
    val portalHostResolvedOnWifi: Boolean?,
)

fun classify(inputs: ClassificationInputs): ConfinementState {
    val vpnKind = VpnKind.fromInterfaces(inputs.vpnInterfaces)
    return when (val bound = inputs.bound) {
        is ProbeResult.Portal -> {
            val host = runCatching { URI(bound.locationUrl).host }.getOrNull()
            val isHostname = host != null && !isIpLiteral(host)
            if (isHostname && inputs.privateDnsStrict && inputs.portalHostResolvedOnWifi == false) {
                ConfinementState.DnsStrict(host!!)
            } else {
                ConfinementState.Confined(bound.locationUrl, bound.capture)
            }
        }
        is ProbeResult.Error -> when {
            bound.message.contains("EPERM") -> ConfinementState.Tunnelled(vpnKind)
            bound.message.contains("EACCES") -> ConfinementState.Blocked(vpnKind)
            else -> ConfinementState.Unknown(bound.message, fallbackMessage(inputs.fallback))
        }
        is ProbeResult.Validated -> ConfinementState.Unknown("bound probe returned 204", fallbackMessage(inputs.fallback))
    }
}

private fun fallbackMessage(fallback: ProbeResult?): String? = when (fallback) {
    is ProbeResult.Error -> fallback.message
    is ProbeResult.Validated -> "default route returned 204"
    is ProbeResult.Portal -> "default route saw the portal"
    null -> null
}

private fun isIpLiteral(host: String): Boolean =
    host.all { it.isDigit() || it == '.' } || host.contains(':')
```

Note: the `host!!` is inside a branch where `isHostname` proved non-null; replace with `requireNotNull(host)` to satisfy the no-`!!` rule.

- [ ] **Step 5: Run tests to verify they pass**

Run: `bash android/run-jvm-tests.sh`
Expected: PASS, including all existing tests.

- [ ] **Step 6: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/network/VpnKind.kt \
        android/app/src/main/java/com/ventouxlabs/gatepath/network/ProbePath.kt \
        android/app/src/main/java/com/ventouxlabs/gatepath/network/ConfinementState.kt \
        android/app/src/test/java/com/ventouxlabs/gatepath/VpnKindTest.kt \
        android/app/src/test/java/com/ventouxlabs/gatepath/ConfinementStateTest.kt \
        android/run-jvm-tests.sh
git commit -m "feat(android): classify captive incidents into a confinement state"
```

---

### Task 3: `ConfinementStateText` — one sentence and one action per state

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/ConfinementStateText.kt`
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/ConfinementStateTextTest.kt`
- Modify: `android/run-jvm-tests.sh` (add both)

**Interfaces:**
- Consumes: `ConfinementState`, `VpnKind` (Task 2).
- Produces:
  - `enum class ConfinementAction { SIGN_IN_HERE, OPEN_VPN_APP, OPEN_NETWORK_SETTINGS, SHARE_EVIDENCE }`
  - `object ConfinementStateText { fun sentence(state: ConfinementState, vpnAppLabel: String?): String; fun action(state: ConfinementState): ConfinementAction; fun actionLabel(action: ConfinementAction): String; fun vpnFallbackLabel(kind: VpnKind): String }`

- [ ] **Step 1: Write the failing test**

```kotlin
package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.ui.ConfinementAction
import com.ventouxlabs.gatepath.ui.ConfinementStateText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfinementStateTextTest {

    private val all = listOf(
        ConfinementState.Confined("http://10.0.0.1/login", null),
        ConfinementState.Tunnelled(VpnKind.TAILSCALE),
        ConfinementState.Blocked(VpnKind.TORGUARD),
        ConfinementState.DnsStrict("n143.network-auth.com"),
        ConfinementState.Unknown("timeout", null),
    )

    @Test
    fun `every state has a non-blank single sentence`() {
        for (s in all) {
            val text = ConfinementStateText.sentence(s, vpnAppLabel = null)
            assertTrue("${s.schemaName} blank", text.isNotBlank())
            assertFalse("${s.schemaName} has a newline", text.contains('\n'))
        }
    }

    @Test
    fun `actions are fixed per state`() {
        assertEquals(ConfinementAction.SIGN_IN_HERE, ConfinementStateText.action(all[0]))
        assertEquals(ConfinementAction.OPEN_VPN_APP, ConfinementStateText.action(all[1]))
        assertEquals(ConfinementAction.OPEN_VPN_APP, ConfinementStateText.action(all[2]))
        assertEquals(ConfinementAction.OPEN_NETWORK_SETTINGS, ConfinementStateText.action(all[3]))
        assertEquals(ConfinementAction.SHARE_EVIDENCE, ConfinementStateText.action(all[4]))
    }

    @Test
    fun `tunnelled names the vpn app label when known and falls back to the kind`() {
        val withLabel = ConfinementStateText.sentence(ConfinementState.Tunnelled(VpnKind.OTHER), "Mullvad")
        assertTrue(withLabel.contains("Mullvad"))
        val fallback = ConfinementStateText.sentence(ConfinementState.Tunnelled(VpnKind.TAILSCALE), null)
        assertTrue(fallback.contains("Tailscale"))
        val generic = ConfinementStateText.sentence(ConfinementState.Blocked(VpnKind.NONE), null)
        assertTrue(generic.contains("your VPN app"))
    }

    @Test
    fun `dns strict names the host and never a url`() {
        val text = ConfinementStateText.sentence(all[3], null)
        assertTrue(text.contains("n143.network-auth.com"))
        assertFalse(text.contains("http"))
    }

    @Test
    fun `confined sentence never contains the portal url`() {
        assertFalse(ConfinementStateText.sentence(all[0], null).contains("10.0.0.1"))
    }

    @Test
    fun `every action has a label`() {
        for (a in ConfinementAction.entries) {
            assertTrue(ConfinementStateText.actionLabel(a).isNotBlank())
        }
    }
}
```

- [ ] **Step 2: Add to runner lists and run to see failure**

`MAIN_SOURCES`: `"$SRC_MAIN/com/ventouxlabs/gatepath/ui/ConfinementStateText.kt"` after `PortalLoadError.kt`.
`TEST_SOURCES`: `"$SRC_TEST/com/ventouxlabs/gatepath/ConfinementStateTextTest.kt"` after `PortalLoadErrorTest.kt`.

Run: `bash android/run-jvm-tests.sh` — Expected: unresolved reference `ConfinementStateText`.

- [ ] **Step 3: Implement**

```kotlin
package com.ventouxlabs.gatepath.ui

import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.VpnKind

enum class ConfinementAction { SIGN_IN_HERE, OPEN_VPN_APP, OPEN_NETWORK_SETTINGS, SHARE_EVIDENCE }

/**
 * The one sentence and one action per [ConfinementState]. Pure Kotlin so the
 * copy is regression-tested like [PortalLoadErrorText]. Never carries a URL:
 * portal URLs embed MAC addresses and session tokens.
 */
object ConfinementStateText {

    fun sentence(state: ConfinementState, vpnAppLabel: String?): String = when (state) {
        is ConfinementState.Confined ->
            "Gatepath is confined to this Wi-Fi. You can sign in here."
        is ConfinementState.Tunnelled ->
            "Your VPN is carrying Gatepath's traffic. Exclude Gatepath in " +
                "${vpnAppLabel ?: vpnFallbackLabel(state.vpnKind)} to sign in here, " +
                "or use the system notification."
        is ConfinementState.Blocked ->
            "Your VPN's kill switch is blocking Gatepath. Exclude Gatepath in " +
                "${vpnAppLabel ?: vpnFallbackLabel(state.vpnKind)} or use the system notification."
        is ConfinementState.DnsStrict ->
            "Private DNS is strict, so ${state.portalHost} cannot be resolved on this Wi-Fi. " +
                "Set Private DNS to Automatic for this sign-in, or use the system notification."
        is ConfinementState.Unknown ->
            "Gatepath could not work out what this network is doing. Share the evidence."
    }

    fun action(state: ConfinementState): ConfinementAction = when (state) {
        is ConfinementState.Confined -> ConfinementAction.SIGN_IN_HERE
        is ConfinementState.Tunnelled, is ConfinementState.Blocked -> ConfinementAction.OPEN_VPN_APP
        is ConfinementState.DnsStrict -> ConfinementAction.OPEN_NETWORK_SETTINGS
        is ConfinementState.Unknown -> ConfinementAction.SHARE_EVIDENCE
    }

    fun actionLabel(action: ConfinementAction): String = when (action) {
        ConfinementAction.SIGN_IN_HERE -> "Sign in here"
        ConfinementAction.OPEN_VPN_APP -> "Open VPN app"
        ConfinementAction.OPEN_NETWORK_SETTINGS -> "Open network settings"
        ConfinementAction.SHARE_EVIDENCE -> "Share evidence"
    }

    fun vpnFallbackLabel(kind: VpnKind): String = when (kind) {
        VpnKind.TAILSCALE -> "Tailscale"
        VpnKind.TORGUARD -> "TorGuard"
        VpnKind.OTHER, VpnKind.NONE -> "your VPN app"
    }
}
```

- [ ] **Step 4: Run tests** — `bash android/run-jvm-tests.sh` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/ui/ConfinementStateText.kt \
        android/app/src/test/java/com/ventouxlabs/gatepath/ConfinementStateTextTest.kt android/run-jvm-tests.sh
git commit -m "feat(android): one sentence and one action per confinement state"
```

---

### Task 4: `CertSummary`, `IncidentEvidence`, bundle rendering and field guards

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/CertSummary.kt`
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/IncidentEvidence.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticsBundle.kt` (add `evidence` parameter and section)
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/CertSummaryTest.kt`
- Test: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/IncidentEvidenceTest.kt`
- Modify: `android/app/src/test/java/com/ventouxlabs/gatepath/diag/DiagnosticsBundleTest.kt` (evidence section tests)
- Modify: `android/run-jvm-tests.sh`

**Interfaces:**
- Consumes: `ConfinementState`, `ProbePath`, `PortalProbeCapture`, `VpnKind`.
- Produces:
  - `data class CertSummary private constructor(primaryError: Int, notBeforeEpochMillis: Long?, notAfterEpochMillis: Long?, selfSigned: Boolean, sha256Fingerprint: String)` with `companion fun of(primaryError: Int, notBefore: Long?, notAfter: Long?, subjectEqualsIssuer: Boolean, derEncoded: ByteArray?): CertSummary`.
  - `data class IncidentEvidence(confinement: String, probePath: ProbePath, probeCapture: PortalProbeCapture?, resolverWifi: List<String>, resolverDoh: List<String>, certSummary: CertSummary?, vpnKind: VpnKind, vpnInterfaces: List<String>, privateDnsStrict: Boolean, bindError: String?, fallbackError: String?)`.
  - `DiagnosticsBundle.build(..., evidence: IncidentEvidence? = null, ...)` renders a `--- Incident evidence ---` section; `bindError`/`fallbackError` go through the existing `redactDiagnosisText` pass.

- [ ] **Step 1: Write the failing `CertSummaryTest`**

```kotlin
package com.ventouxlabs.gatepath.diag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Modifier

class CertSummaryTest {

    @Test
    fun `fingerprint is lowercase hex sha256 of the der bytes`() {
        val s = CertSummary.of(3, 1L, 2L, subjectEqualsIssuer = true, derEncoded = byteArrayOf(1, 2, 3))
        assertEquals("039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81", s.sha256Fingerprint)
        assertTrue(s.selfSigned)
    }

    @Test
    fun `missing der yields an empty fingerprint not a crash`() {
        assertEquals("", CertSummary.of(0, null, null, false, null).sha256Fingerprint)
    }

    @Test
    fun `every field is an enum number date boolean or fingerprint`() {
        val declared = CertSummary::class.java.declaredFields
            .filterNot { Modifier.isStatic(it.modifiers) }.map { it.name }.toSet()
        assertEquals(
            "CertSummary fields changed. Subject and issuer strings are gateway-authored and must never be added.",
            setOf("primaryError", "notBeforeEpochMillis", "notAfterEpochMillis", "selfSigned", "sha256Fingerprint"),
            declared,
        )
    }
}
```

- [ ] **Step 2: Write the failing `IncidentEvidenceTest`**

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.PortalProbeCapture
import com.ventouxlabs.gatepath.network.ProbePath
import com.ventouxlabs.gatepath.network.VpnKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Modifier

class IncidentEvidenceTest {

    private val meta = BundleMeta("2026-09-19T00:00:00Z", "1.1.0", 3, "16", 36)

    private fun evidence(bindError: String? = null) = IncidentEvidence(
        confinement = "tunnelled",
        probePath = ProbePath.BOUND_WIFI,
        probeCapture = PortalProbeCapture.of(302, "text/html", PortalProbeCapture.RedirectSignal.LOCATION_HEADER),
        resolverWifi = listOf("10.0.0.1"),
        resolverDoh = listOf("93.184.216.34"),
        certSummary = CertSummary.of(3, 1L, 2L, true, byteArrayOf(9)),
        vpnKind = VpnKind.TAILSCALE,
        vpnInterfaces = listOf("tailscale0 (split_tunnel)"),
        privateDnsStrict = false,
        bindError = bindError,
        fallbackError = null,
    )

    @Test
    fun `field set is guarded`() {
        val declared = IncidentEvidence::class.java.declaredFields
            .filterNot { Modifier.isStatic(it.modifiers) }.map { it.name }.toSet()
        assertEquals(
            "IncidentEvidence fields changed. Every field is exported in the shared bundle; " +
                "a new one must be an enum, number, boolean, date, fingerprint, or pass through redaction.",
            setOf(
                "confinement", "probePath", "probeCapture", "resolverWifi", "resolverDoh",
                "certSummary", "vpnKind", "vpnInterfaces", "privateDnsStrict", "bindError", "fallbackError",
            ),
            declared,
        )
    }

    @Test
    fun `bundle renders the evidence section with the state and path`() {
        val out = DiagnosticsBundle.build(meta, emptyList(), null, evidence = evidence(), redact = false)
        assertTrue(out.contains("--- Incident evidence ---"))
        assertTrue(out.contains("confinement: tunnelled"))
        assertTrue(out.contains("probe_path: BOUND_WIFI"))
        assertTrue(out.contains("vpn_kind: TAILSCALE"))
        assertTrue(out.contains("cert_self_signed: true"))
    }

    @Test
    fun `redaction masks resolver answers and error text ip literals`() {
        val out = DiagnosticsBundle.build(
            meta, emptyList(), null,
            evidence = evidence(bindError = "connect to /10.0.0.1:80 failed: EPERM"), redact = true,
        )
        assertFalse(out.contains("10.0.0.1"))
        assertFalse(out.contains("93.184.216.34"))
        assertTrue(out.contains("EPERM"))
    }

    @Test
    fun `absent evidence is stated in prose`() {
        val out = DiagnosticsBundle.build(meta, emptyList(), null, evidence = null, redact = true)
        assertTrue(out.contains("(no incident evidence captured)"))
    }
}
```

- [ ] **Step 3: Add to the runner and run to see failure**

`MAIN_SOURCES` after `DiagnosticsBundle.kt`: `CertSummary.kt`, `IncidentEvidence.kt` (both under `diag/`).
`TEST_SOURCES` after `DiagnosticsBundleTest.kt`: `diag/CertSummaryTest.kt`, `diag/IncidentEvidenceTest.kt`.

Run: `bash android/run-jvm-tests.sh` — Expected: unresolved references.

- [ ] **Step 4: Implement `CertSummary.kt`**

```kotlin
package com.ventouxlabs.gatepath.diag

import java.security.MessageDigest

/**
 * What a certificate error looked like, without anything the gateway wrote.
 * Subject and issuer are gateway-authored strings, so they are not here; the
 * SHA-256 fingerprint identifies the certificate without echoing it.
 * `CertSummaryTest` guards the field set.
 */
@ConsistentCopyVisibility
data class CertSummary private constructor(
    /** `android.net.http.SslError` primary error code (0..5). */
    val primaryError: Int,
    val notBeforeEpochMillis: Long?,
    val notAfterEpochMillis: Long?,
    val selfSigned: Boolean,
    /** Lowercase hex; empty when the DER bytes were unavailable. */
    val sha256Fingerprint: String,
) {
    companion object {
        fun of(
            primaryError: Int,
            notBefore: Long?,
            notAfter: Long?,
            subjectEqualsIssuer: Boolean,
            derEncoded: ByteArray?,
        ): CertSummary = CertSummary(
            primaryError = primaryError,
            notBeforeEpochMillis = notBefore,
            notAfterEpochMillis = notAfter,
            selfSigned = subjectEqualsIssuer,
            sha256Fingerprint = derEncoded?.let { sha256Hex(it) } ?: "",
        )

        private fun sha256Hex(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    }
}
```

- [ ] **Step 5: Implement `IncidentEvidence.kt`**

```kotlin
package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.PortalProbeCapture
import com.ventouxlabs.gatepath.network.ProbePath
import com.ventouxlabs.gatepath.network.VpnKind

/**
 * One record per captive incident, produced in every [ConfinementState], not
 * only when a session opens. This is the debugging artefact; the audit log
 * stays a session log. Free-text fields ([bindError], [fallbackError]) are
 * rendered through the bundle's redaction pass; everything else is an enum,
 * number, boolean, date or fingerprint. `IncidentEvidenceTest` guards the set.
 */
data class IncidentEvidence(
    val confinement: String,
    val probePath: ProbePath,
    val probeCapture: PortalProbeCapture?,
    /** IP literals the Wi-Fi network's resolver returned for the probe host; empty = failed/not run. */
    val resolverWifi: List<String>,
    /** IP literals DoH (1.1.1.1) returned; empty = failed/not run/declined. */
    val resolverDoh: List<String>,
    val certSummary: CertSummary?,
    val vpnKind: VpnKind,
    val vpnInterfaces: List<String>,
    val privateDnsStrict: Boolean,
    val bindError: String?,
    val fallbackError: String?,
)
```

- [ ] **Step 6: Extend `DiagnosticsBundle.build`**

Add parameter `evidence: IncidentEvidence? = null` after `probeCapture`, and after the probe-capture section:

```kotlin
        appendLine("--- Incident evidence ---")
        val evidenceText = renderEvidence(evidence)
        appendLine(if (redact) redactDiagnosisText(evidenceText, entries) else evidenceText)
        appendLine()
```

and the renderer:

```kotlin
    private fun renderEvidence(e: IncidentEvidence?): String {
        if (e == null) return "(no incident evidence captured)"
        return buildString {
            appendLine("confinement: ${e.confinement}")
            appendLine("probe_path: ${e.probePath}")
            appendLine("vpn_kind: ${e.vpnKind}")
            appendLine("vpn_interfaces: ${if (e.vpnInterfaces.isEmpty()) "(none)" else e.vpnInterfaces.joinToString(", ")}")
            appendLine("private_dns_strict: ${e.privateDnsStrict}")
            appendLine("resolver_wifi: ${if (e.resolverWifi.isEmpty()) "(none)" else e.resolverWifi.joinToString(", ")}")
            appendLine("resolver_doh: ${if (e.resolverDoh.isEmpty()) "(none)" else e.resolverDoh.joinToString(", ")}")
            val c = e.certSummary
            if (c == null) {
                appendLine("cert: (no certificate error observed)")
            } else {
                appendLine("cert_primary_error: ${c.primaryError}")
                appendLine("cert_not_before_epoch_ms: ${c.notBeforeEpochMillis ?: "(absent)"}")
                appendLine("cert_not_after_epoch_ms: ${c.notAfterEpochMillis ?: "(absent)"}")
                appendLine("cert_self_signed: ${c.selfSigned}")
                appendLine("cert_sha256: ${c.sha256Fingerprint.ifEmpty { "(absent)" }}")
            }
            appendLine("bind_error: ${e.bindError ?: "(none)"}")
            append("fallback_error: ${e.fallbackError ?: "(none)"}")
        }
    }
```

The IPv4 mask in `redactDiagnosisText` already covers resolver answers. Add an IPv6 mask too: `private val IPV6 = Regex("""\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b""")` applied after IPV4.

- [ ] **Step 7: Run tests** — `bash android/run-jvm-tests.sh` — Expected: PASS. If the IPv6 regex masks the `2026-09-19T00:00:00Z` timestamp, anchor it to require at least two `::`-free hex groups with a colon and no `T`; adjust until `header carries app and platform metadata` still passes.

- [ ] **Step 8: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/diag/CertSummary.kt \
        android/app/src/main/java/com/ventouxlabs/gatepath/diag/IncidentEvidence.kt \
        android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticsBundle.kt \
        android/app/src/test/java/com/ventouxlabs/gatepath/diag/CertSummaryTest.kt \
        android/app/src/test/java/com/ventouxlabs/gatepath/diag/IncidentEvidenceTest.kt \
        android/run-jvm-tests.sh
git commit -m "feat(android): incident evidence record with a privacy-safe certificate summary"
```

---

### Task 5: Audit schema v2 — contract JSON and the Android writer/reader

**Files:**
- Modify: `docs/audit_log_schema.json`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/audit/AuditEntry.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/audit/AuditLog.kt:76-100` (`readRecent`)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/MainViewModel.kt:421-435` (writer call site)
- Modify: `android/app/src/test/java/com/ventouxlabs/gatepath/AuditSchemaParityTest.kt`, `AuditLogTest.kt`, `diag/DiagnosticsBundleTest.kt` (constructor arguments)

**Interfaces:**
- Produces: JSON keys `schema_version: 2`, `confinement` (required, enum `confined|unconfined`), `observed_navigation_attempts`, `observed_resource_requests` (replacing `blocked_*`); Kotlin `AuditEntry.confinement: String`, `observedNavigationAttempts`, `observedResourceRequests`. Reader maps v1 `blocked_*` keys before decoding.

- [ ] **Step 1: Write the failing parity tests**

In `AuditSchemaParityTest.kt` change `sampleEntry()` to pass `schemaVersion = 2`, `confinement = "confined"`, `observedNavigationAttempts = 2`, `observedResourceRequests = 11`, and add:

```kotlin
    @Test
    fun `confinement value is in the schema confinement_enum`() = runBlocking {
        writer.append(sampleEntry())
        val obj = readWrittenJson()
        val allowed = schema["confinement_enum"]!!.jsonArray.map { it.jsonPrimitive.content }.toSet()
        assertTrue(obj["confinement"]!!.jsonPrimitive.content in allowed)
    }

    @Test
    fun `schema_version written is the schema's version`() = runBlocking {
        writer.append(sampleEntry())
        assertEquals(schema["schema_version"]!!.jsonPrimitive.int, readWrittenJson()["schema_version"]!!.jsonPrimitive.int)
    }
```

In `AuditLogTest.kt` add, using the existing temp-file writer fixture:

```kotlin
    @Test
    fun `a v1 line with blocked_ keys reads back as observed_ counters`() {
        logFile.appendText(
            """{"schema_version":1,"timestamp_utc":"2026-05-06T12:34:56.000Z","platform":"android","ssid":null,"gateway_ip":null,"portal_domain":"p.example","vpn_interfaces_detected":[],"vpn_warning_shown":false,"session_opened_utc":"2026-05-06T12:34:00.000Z","session_closed_utc":"2026-05-06T12:36:42.000Z","close_reason":"portal_completed","duration_seconds":162,"blocked_navigation_attempts":4,"blocked_resource_requests":9}""" + "\n",
        )
        val read = writer.readRecent()
        assertEquals(0, read.unreadable)
        assertEquals(4, read.entries.single().observedNavigationAttempts)
        assertEquals(9, read.entries.single().observedResourceRequests)
        assertEquals("unconfined", read.entries.single().confinement)
    }
```

Run: `bash android/run-jvm-tests.sh` — Expected: compile failure on the new constructor parameters.

- [ ] **Step 2: Update the contract JSON**

Set `"schema_version": 2`. In `required_fields` replace `blocked_navigation_attempts` → `observed_navigation_attempts`, `blocked_resource_requests` → `observed_resource_requests`, and append `"confinement"`. Update `field_types` the same way and add `"confinement": "string"`. Add:

```json
  "confinement_enum": ["confined", "unconfined"],
  "v1_key_renames": {
    "blocked_navigation_attempts": "observed_navigation_attempts",
    "blocked_resource_requests": "observed_resource_requests"
  },
```

Rename the two keys inside `platform_specific_semantics`, and add:

```json
    "confinement": {
      "android": "Always 'confined': an Android session opens only when the Wi-Fi-bound probe reached the gateway, which netd permits only when no secure VPN covers Gatepath (or Gatepath is excluded from it). See SECURITY_MODEL.md.",
      "desktop": "'confined' when the netns helper launched the portal WebView inside the gatepath namespace; 'unconfined' for the in-process (Flatpak-only) path, where WebKitGTK traffic follows the system default route."
    }
```

Update `$comment` to mention v2 and that readers map `v1_key_renames`.

- [ ] **Step 3: Update `AuditEntry.kt`**

```kotlin
    @SerialName("schema_version") val schemaVersion: Int = 2,
    ...
    @SerialName("observed_navigation_attempts") val observedNavigationAttempts: Int,
    @SerialName("observed_resource_requests") val observedResourceRequests: Int,
    /** `confined` or `unconfined` — see docs/audit_log_schema.json `confinement_enum`. Android always writes `confined`. */
    @SerialName("confinement") val confinement: String = "unconfined",
```

Keep `tlsCertErrorsBypassed` as is. Update the class KDoc to "Schema version 2".

- [ ] **Step 4: Map v1 keys in `AuditLogWriter.readRecent`**

Replace the decode lambda body with:

```kotlin
            runCatching { readerJson.decodeFromString<AuditEntry>(upgradeV1(line)) }
```

and add:

```kotlin
    /**
     * v1 lines carry `blocked_*` counters; v2 renamed them to `observed_*`
     * (the semantics changed in PR #33, the names could not until a version
     * bump). Rewrite the keys so a v1 line decodes with its counts intact.
     * Names are duplicated from docs/audit_log_schema.json `v1_key_renames`;
     * AuditSchemaParityTest pins them.
     */
    private fun upgradeV1(line: String): String {
        val obj = runCatching { Json.parseToJsonElement(line).jsonObject }.getOrNull() ?: return line
        if (obj["schema_version"]?.jsonPrimitive?.intOrNull != 1) return line
        val renamed = obj.entries.associate { (k, v) -> (V1_RENAMES[k] ?: k) to v }
        return JsonObject(renamed).toString()
    }

    private companion object {
        val V1_RENAMES = mapOf(
            "blocked_navigation_attempts" to "observed_navigation_attempts",
            "blocked_resource_requests" to "observed_resource_requests",
        )
    }
```

Add a parity test in `AuditSchemaParityTest`:

```kotlin
    @Test
    fun `reader's v1 renames match the schema's v1_key_renames`() {
        val schemaRenames = schema["v1_key_renames"]!!.jsonObject.mapValues { it.value.jsonPrimitive.content }
        assertEquals(schemaRenames, AuditLogWriter.v1KeyRenamesForTest())
    }
```

and expose `internal fun v1KeyRenamesForTest(): Map<String, String> = V1_RENAMES` on the companion (internal is visible to tests via `-Xfriend-paths`).

- [ ] **Step 5: Update the writer call site and fixtures**

`MainViewModel.writeAuditLog`: `observedNavigationAttempts = finalState.blockedNavigationAttempts`, `observedResourceRequests = finalState.blockedResourceRequests`, `confinement = "confined"`. Kotlin `PortalSession` counter names stay `blocked*` (internal, not schema). Update `AuditLogTest`, `DiagnosticsBundleTest.entry(...)` constructor arguments to the new names.

- [ ] **Step 6: Run** `bash android/run-jvm-tests.sh` — Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add docs/audit_log_schema.json android/app/src/main/java/com/ventouxlabs/gatepath/audit android/app/src/main/java/com/ventouxlabs/gatepath/MainViewModel.kt android/app/src/test/java/com/ventouxlabs/gatepath
git commit -m "feat(audit): schema v2 with confinement and observed_* counters (android)"
```

---

### Task 6: Audit schema v2 — desktop writer and confinement source

**Files:**
- Modify: `desktop/gatepath/portal_session.py` (add `Confinement` enum + field)
- Modify: `desktop/gatepath/audit_log.py:80-101`
- Modify: `desktop/gatepath/window.py:342` and `:395`
- Test: `desktop/tests/test_audit_log.py`, `desktop/tests/test_session.py`, `desktop/tests/test_session_controller.py`

**Interfaces:**
- Produces: `class Confinement(str, Enum): CONFINED = "confined"; UNCONFINED = "unconfined"`; `PortalSession.confinement: Confinement = Confinement.UNCONFINED`; writer emits `schema_version: 2`, `confinement`, `observed_*`.

- [ ] **Step 1: Write the failing tests**

In `test_audit_log.py` update `test_field_types_match_schema` to `assert entry["schema_version"] == 2` and the two counter assertions to `observed_*`; add:

```python
    def test_confinement_defaults_to_unconfined_and_is_in_enum(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        write_session(_make_completed_session(), log_path=log)
        entry = read_all(log_path=log)[0]
        assert entry["confinement"] == "unconfined"
        assert entry["confinement"] in set(_SCHEMA["confinement_enum"])

    def test_confined_session_writes_confined(self, tmp_path: Path) -> None:
        import dataclasses
        from gatepath.portal_session import Confinement
        log = tmp_path / "audit.jsonl"
        write_session(
            dataclasses.replace(_make_completed_session(), confinement=Confinement.CONFINED),
            log_path=log,
        )
        assert read_all(log_path=log)[0]["confinement"] == "confined"

    def test_every_confinement_value_is_in_the_schema_enum(self) -> None:
        from gatepath.portal_session import Confinement
        for c in Confinement:
            assert c.value in set(_SCHEMA["confinement_enum"])
```

Change `_make_completed_session()` kwargs and assertions in `test_session_controller.py:198-240` and `test_session.py` from `blocked_navigation_attempts`/`blocked_resource_requests` JSON keys to `observed_*` **only where they index the written entry**; the `PortalSession` attribute names and `to_completed` kwargs are unchanged.

Run: `cd desktop && python -m pytest tests/test_audit_log.py -q` — Expected: FAIL (`confinement` missing, version 1).

- [ ] **Step 2: Implement**

`portal_session.py`, after `CloseReason`:

```python
class Confinement(str, Enum):
    """Whether the portal WebView ran inside the gatepath netns.

    Wire values; see docs/audit_log_schema.json `confinement_enum`.
    """

    CONFINED = "confined"
    UNCONFINED = "unconfined"
```

and on `PortalSession`: `confinement: Confinement = Confinement.UNCONFINED` (after `tls_cert_errors_bypassed`).

`audit_log.py` entry dict: `"schema_version": 2`, rename the two counter keys to `observed_navigation_attempts` / `observed_resource_requests` (values unchanged), add `"confinement": session.confinement.value`. Update the module docstring to v2.

`window.py`: at line 395 (isolated path) replace `self._controller.set_active(active_session)` with:

```python
            self._controller.set_active(
                dataclasses.replace(active_session, confinement=Confinement.CONFINED)
            )
```

adding `import dataclasses` and `Confinement` to the `portal_session` import. The in-process path at line 342 keeps the default `UNCONFINED`.

- [ ] **Step 3: Run** `cd desktop && python -m pytest tests/ -q` — Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add desktop/gatepath/portal_session.py desktop/gatepath/audit_log.py desktop/gatepath/window.py desktop/tests
git commit -m "feat(audit): schema v2 with confinement and observed_* counters (desktop)"
```

---

### Task 7: Rename the remaining `blocked_*` consumers and document v2

**Files:**
- Modify: `docs/AUDIT_LOG_SCHEMA.md`
- Modify: `docs/CODEMAPS/data.md:22-23`
- Modify: `tests/e2e-android/driver/assertions.py:288`
- Modify: `tests/e2e-android/driver/test_assertions.py:83-84`

- [ ] **Step 1: Update the harness assertion and its test**

In `assertions.py` `check_off_domain`, the counted-field tuple becomes `("observed_navigation_attempts", "observed_resource_requests")`. In `test_assertions.py` rename the keys in `AUDIT_COUNTED` / `AUDIT_ZERO`.

Run: `cd tests/e2e-android && python -m pytest driver -q` — Expected: PASS.

- [ ] **Step 2: Rewrite `AUDIT_LOG_SCHEMA.md`**

Example block: `"schema_version": 2`, `"observed_navigation_attempts": 2`, `"observed_resource_requests": 11`, add `"confinement": "confined"`. Field table: rename the two rows (drop the "Field name retained" clauses), add a `confinement` row: `"confined" \| "unconfined"` with the two platform semantics from the JSON. Replace the "Field-name caveat" section with a short "Version history" section: v1 (2026-05), v2 (this change: rename + `confinement`; readers map `v1_key_renames`). Fix the `tls_cert_errors_bypassed` row, which still says desktop is always 0 (it is not, since #123).

- [ ] **Step 3: Update `docs/CODEMAPS/data.md:22-23`** to the new names and add `confinement: "confined" | "unconfined"`.

- [ ] **Step 4: Commit**

```bash
git add docs/AUDIT_LOG_SCHEMA.md docs/CODEMAPS/data.md tests/e2e-android/driver
git commit -m "docs(audit): document schema v2 and rename the harness counters"
```

---

### Task 8: Monitor emits one `CaptiveIncident` event carrying classification inputs

Android-only file; verified by `./gradlew :app:assembleDebug` in CI (`android.yml`) and by the JVM tests of the pure parts it calls.

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/network/CaptivePortalMonitor.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/network/NetworkDiagnostics.kt:15-30` (comments only)

**Interfaces:**
- Consumes: `ClassificationInputs`, `ProbePath` (Task 2), `NetworkDiagnostics`, `VpnDetector`.
- Produces:

```kotlin
    /**
     * Every captive-looking network produces exactly one of these per
     * evaluation. The ViewModel classifies it; the monitor does not decide.
     */
    data class CaptiveIncident(
        val network: Network,
        val inputs: ClassificationInputs,
        val boundPath: ProbePath,
        val diagnostics: NetworkDiagnostics,
    ) : NetworkEvent
```

`NetworkEvent.CaptiveNetworkAvailable` and `NetworkEvent.CaptivePortalSuspected` are removed.

- [ ] **Step 1: Replace the two events with `CaptiveIncident`**

In the `sealed interface NetworkEvent` block delete `CaptiveNetworkAvailable` and `CaptivePortalSuspected`; add `CaptiveIncident` as above.

- [ ] **Step 2: Rewrite `probeAndEmit`**

```kotlin
        fun probeAndEmit(network: Network) {
            if (!probed.add(network)) return
            ioScope.launch {
                Log.d(TAG, "Probing network $network (bind path)")
                val previousBinding = connectivityManager.boundNetworkForProcess
                val bindResult = try {
                    connectivityManager.bindProcessToNetwork(network)
                    probe.probe(network, testUrl = probeUrl)
                } finally {
                    connectivityManager.bindProcessToNetwork(previousBinding)
                }
                if (bindResult is ProbeResult.Validated) {
                    // Capability said NOT validated; the Wi-Fi itself answered 204. Not an incident.
                    Log.d(TAG, "Network $network probed validated despite NOT_VALIDATED capability")
                    return@launch
                }
                // The default-route probe is evidence, never the decision: it
                // shows whether some other path (VPN, cellular) has internet.
                val fallbackResult = probe.probe(network = null, testUrl = probeUrl)
                val linkProps = runCatching { connectivityManager.getLinkProperties(network) }.getOrNull()
                val privateDnsStrict = linkProps?.privateDnsServerName != null
                val resolved = (bindResult as? ProbeResult.Portal)?.let { resolvePortalHostOnWifi(network, it.locationUrl) }
                val diagnostics = buildDiagnostics(
                    network = network,
                    bindError = (bindResult as? ProbeResult.Error)?.message,
                    fallbackError = (fallbackResult as? ProbeResult.Error)?.message,
                    defaultRouteBypassesCaptive = fallbackResult is ProbeResult.Validated,
                )
                val inputs = ClassificationInputs(
                    bound = bindResult,
                    fallback = fallbackResult,
                    vpnInterfaces = diagnostics.vpnInterfaces,
                    privateDnsStrict = privateDnsStrict,
                    portalHostResolvedOnWifi = resolved,
                )
                if (bindResult is ProbeResult.Portal) captive.add(network)
                Log.i(TAG, "Captive incident on $network: bound=${bindResult::class.simpleName}")
                trySend(NetworkEvent.CaptiveIncident(network, inputs, ProbePath.BOUND_WIFI, diagnostics))
                // Allow re-probing on the next capability change unless we found the portal.
                if (bindResult !is ProbeResult.Portal) probed.remove(network)
            }
        }
```

Add the helper (null when the URL has no hostname or the host is an IP literal):

```kotlin
    /**
     * Resolve the portal hostname through THIS network's resolver. Under strict
     * Private DNS the lookup goes to the DoT server, which the captive gateway
     * blocks, so failure here plus strict mode is the DnsStrict signal.
     */
    private fun resolvePortalHostOnWifi(network: Network, portalUrl: String): Boolean? {
        val host = runCatching { java.net.URI(portalUrl).host }.getOrNull() ?: return null
        if (host.all { it.isDigit() || it == '.' } || host.contains(':')) return null
        return runCatching { network.getAllByName(host).isNotEmpty() }.getOrDefault(false)
    }
```

- [ ] **Step 3: Fix the comments**

In `CaptivePortalMonitor.kt` class KDoc replace the bullet "Fails with `EPERM` on captive networks because Android marks them restricted" with "Fails with `EPERM` when a secure VPN covers this UID (netd `checkUserNetworkAccess`); see `ConfinementState`." Delete the "Fails with EPERM on captive (restricted) networks" comment in `probeAndEmit`. In `NetworkDiagnostics.kt` replace the `bindProbeError` KDoc with "`EPERM` when a secure VPN covers Gatepath's UID; `EACCES` under always-on lockdown; `null` when the bound probe reached the gateway."

- [ ] **Step 4: Compile check**

Run: `cd android && ./gradlew :app:compileDebugKotlin` if `ANDROID_HOME` is set; otherwise rely on CI. The ViewModel will not compile until Task 9; do Tasks 8 and 9 in one commit if working without a local SDK is impossible to verify in between.

- [ ] **Step 5: Commit** (with Task 9 if needed)

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/network
git commit -m "refactor(android): monitor reports one captive incident with classification inputs"
```

---

### Task 9: ViewModel classifies, gates the session, publishes evidence

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/MainViewModel.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/MainActivity.kt` (collect the new flows, pass evidence to the sharer)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/share/DiagnosticsSharer.kt` (`evidence` parameter)

**Interfaces:**
- Consumes: `CaptiveIncident` (Task 8), `classify` (Task 2), `IncidentEvidence`/`CertSummary` (Task 4).
- Produces on `MainViewModel`:
  - `val confinement: StateFlow<ConfinementState?>`
  - `val evidence: StateFlow<IncidentEvidence?>`
  - `fun onCertSummary(summary: CertSummary)` — called by the WebView (Task 12).
  - `fun signInHere()` — opens the session from `Confined`.
  - Removed: `NetworkStatus.CaptivePending`, `latestDiagnostics`, `latestProbeCapture`.
  - Debug-only side effect: writes `files/confinement-state.txt` containing `state.schemaName` on every classification (harness artefact, Task 14).

- [ ] **Step 1: Replace the two event branches**

Replace the `CaptiveNetworkAvailable` and `CaptivePortalSuspected` branches in `observeNetwork()` with:

```kotlin
                    is NetworkEvent.CaptiveIncident -> handleIncident(event)
```

and add:

```kotlin
    private fun handleIncident(event: NetworkEvent.CaptiveIncident) {
        val state = classify(event.inputs)
        _confinement.value = state
        _activeNetwork.value = event.network
        _networkStatus.value = NetworkStatus.CaptiveDetected
        _diagnosis.value = null
        suspectedNetwork = event.network
        _evidence.value = IncidentEvidence(
            confinement = state.schemaName,
            probePath = event.boundPath,
            probeCapture = (event.inputs.bound as? ProbeResult.Portal)?.capture,
            resolverWifi = emptyList(),
            resolverDoh = emptyList(),
            certSummary = null,
            vpnKind = VpnKind.fromInterfaces(event.diagnostics.vpnInterfaces),
            vpnInterfaces = event.diagnostics.vpnInterfaces,
            privateDnsStrict = event.diagnostics.privateDnsServer != null,
            bindError = event.diagnostics.bindProbeError,
            fallbackError = event.diagnostics.fallbackProbeError,
        )
        debugStateSink?.invoke(state.schemaName)
        Log.i(TAG, "Confinement on ${event.network}: ${state.schemaName}")
        if (state is ConfinementState.Confined) {
            _session.value = sessionManager.portalDetected(_session.value, state.portalUrl)
            openPortal()
        }
        runDiagnosticEngine(event.network, event.diagnostics)
    }
```

Declare the flows next to the existing ones:

```kotlin
    private val _confinement = MutableStateFlow<ConfinementState?>(null)
    val confinement: StateFlow<ConfinementState?> = _confinement.asStateFlow()

    private val _evidence = MutableStateFlow<IncidentEvidence?>(null)
    val evidence: StateFlow<IncidentEvidence?> = _evidence.asStateFlow()

    /** Debug-only hook the harness reads as a file; null in release. Set by MainActivity. */
    @Volatile var debugStateSink: ((String) -> Unit)? = null
```

Remove `_latestDiagnostics`, `_latestProbeCapture`, `NetworkStatus.CaptivePending` and its KDoc; `clearIncidentState()` now clears `_confinement`, `_evidence`, `_diagnosis`, `suspectedNetwork`. `rerunDiagnostics()` keeps working from `suspectedNetwork` using the last evidence's `bindError`/`fallbackError`.

- [ ] **Step 2: Feed the resolver answers into the evidence**

In `runDiagnosticEngine`, the `resolveHost` lambda resolves on the captive network when `_confinement.value is ConfinementState.Confined`, otherwise as today:

```kotlin
                resolveHost = { host ->
                    runCatching {
                        val addrs = if (_confinement.value is ConfinementState.Confined) {
                            network.getAllByName(host)
                        } else {
                            InetAddress.getAllByName(host)
                        }
                        addrs.mapNotNull { it.hostAddress }
                    }.getOrElse { emptyList() }.also { answers ->
                        _evidence.update { it?.copy(resolverWifi = answers) }
                    }
                },
```

After `diagnosticEngine.run(ctx)` returns, copy the DoH answer from a `DiagnosticReport.DnsHijack` check into `resolverDoh` (`listOf(report.doHAnswer)`) via `_evidence.update`. Import `kotlinx.coroutines.flow.update`.

- [ ] **Step 3: Add `onCertSummary` and `signInHere`**

```kotlin
    fun onCertSummary(summary: CertSummary) {
        _evidence.update { it?.copy(certSummary = summary) }
    }

    /** User pressed "Sign in here". Only meaningful from Confined; otherwise a no-op. */
    fun signInHere() {
        val state = _confinement.value as? ConfinementState.Confined ?: return
        if (_session.value is PortalSession.Active) return
        _session.value = sessionManager.portalDetected(_session.value, state.portalUrl)
        openPortal()
    }
```

`handleIncident` opens the session automatically from `Confined` (today's behaviour); `signInHere` exists for the card after a dismiss.

- [ ] **Step 4: MainActivity and sharer plumbing**

`MainActivity.onCreate`: before `setContent`, in debug builds only:

```kotlin
        if (BuildConfig.DEBUG) {
            viewModel.debugStateSink = { name ->
                File(filesDir, DEBUG_STATE_FILE).writeText(name)
            }
        }
```

with `private const val DEBUG_STATE_FILE = "confinement-state.txt"` in the companion (keep in sync with `run-scenario.py`, Task 14). Collect `confinement` and `evidence` with `collectAsState()` and pass them to `MainScreen` (Task 10) and to `shareDiagnostics(redact, evidence)`. `DiagnosticsSharer.writeBundle` gains `evidence: IncidentEvidence?` and forwards it to `DiagnosticsBundle.build`. `debugWriteDiagnosticsBundle` passes `viewModel.evidence.value`.

- [ ] **Step 5: Build** — CI `android.yml` (`assembleDebug` + `testDebugUnitTest`); locally if SDK present.

- [ ] **Step 6: Commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath
git commit -m "feat(android): classify each captive incident and gate sign-in on Confined"
```

---

### Task 10: The confinement card replaces the troubleshooting panel

**Files:**
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/ConfinementCard.kt`
- Create: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/VpnAppLauncher.kt`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/MainScreen.kt` (delete `TroubleshootingPanel`, `DiagnosticRow`, `recoverySteps`, the `CaptivePending` branches; add the card)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/MainActivity.kt` (new `MainScreen` parameters)

**Interfaces:**
- Consumes: `ConfinementState`, `ConfinementStateText`, `ConfinementAction`, `VpnKind`.
- Produces:
  - `@Composable fun ConfinementCard(state: ConfinementState, vpnAppLabel: String?, onAction: (ConfinementAction) -> Unit, onShareEvidence: () -> Unit)`
  - `object VpnAppLauncher { fun resolve(context: Context, kind: VpnKind): Pair<String?, Intent> }` returning the app label and a launch intent, falling back to `Intent(Settings.ACTION_VPN_SETTINGS)`.
  - `MainScreen(session, networkStatus, confinement: ConfinementState?, vpnAppLabel: String?, diagnosis, onDismiss, onAction: (ConfinementAction) -> Unit, onRunDiagnostics, onShareDiagnostics)`.

- [ ] **Step 1: `VpnAppLauncher.kt`**

```kotlin
package com.ventouxlabs.gatepath.ui

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.VpnService
import android.provider.Settings
import com.ventouxlabs.gatepath.network.VpnKind

/** Finds the VPN app to send the user to. Known packages first, then any installed VpnService. */
object VpnAppLauncher {
    private val KNOWN = mapOf(
        VpnKind.TAILSCALE to listOf("com.tailscale.ipn"),
        VpnKind.TORGUARD to listOf("net.torguard.openvpn.client"),
    )

    fun resolve(context: Context, kind: VpnKind): Pair<String?, Intent> {
        val pm = context.packageManager
        val candidates = KNOWN[kind].orEmpty() + installedVpnPackages(pm)
        for (pkg in candidates) {
            val launch = pm.getLaunchIntentForPackage(pkg) ?: continue
            val label = runCatching { pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString() }.getOrNull()
            return label to launch
        }
        return null to Intent(Settings.ACTION_VPN_SETTINGS)
    }

    private fun installedVpnPackages(pm: PackageManager): List<String> =
        pm.queryIntentServices(Intent(VpnService.SERVICE_INTERFACE), PackageManager.GET_META_DATA)
            .map { it.serviceInfo.packageName }
            .distinct()
}
```

`queryIntentServices` for `android.net.VpnService` needs a `<queries>` block on Android 11+. Add to `AndroidManifest.xml` before `<application>`:

```xml
    <queries>
        <intent>
            <action android:name="android.net.VpnService" />
        </intent>
        <package android:name="com.tailscale.ipn" />
        <package android:name="net.torguard.openvpn.client" />
    </queries>
```

- [ ] **Step 2: `ConfinementCard.kt`**

```kotlin
package com.ventouxlabs.gatepath.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.ventouxlabs.gatepath.network.ConfinementState

@Composable
fun ConfinementCard(
    state: ConfinementState,
    vpnAppLabel: String?,
    onAction: (ConfinementAction) -> Unit,
    onShareEvidence: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val action = ConfinementStateText.action(state)
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surfaceVariant,
        tonalElevation = 2.dp,
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(ConfinementStateText.sentence(state, vpnAppLabel), style = MaterialTheme.typography.bodyLarge)
            Button(onClick = { onAction(action) }) { Text(ConfinementStateText.actionLabel(action)) }
            if (action != ConfinementAction.SHARE_EVIDENCE) {
                TextButton(onClick = onShareEvidence) {
                    Text(ConfinementStateText.actionLabel(ConfinementAction.SHARE_EVIDENCE))
                }
            }
        }
    }
}
```

- [ ] **Step 3: Rewire `MainScreen`**

Replace the three `CaptivePending` blocks (lines 95-108) with:

```kotlin
        confinement?.let { state ->
            Spacer(modifier = Modifier.height(24.dp))
            ConfinementCard(
                state = state,
                vpnAppLabel = vpnAppLabel,
                onAction = { action ->
                    if (action == ConfinementAction.SHARE_EVIDENCE) showShareDialog = true else onAction(action)
                },
                onShareEvidence = { showShareDialog = true },
            )
            if (diagnosis != null) {
                Spacer(modifier = Modifier.height(16.dp))
                DiagnosisPanel(diagnosis = diagnosis)
            }
            Spacer(modifier = Modifier.height(8.dp))
            TextButton(onClick = onRunDiagnostics) { Text("Run diagnostics again") }
        }
```

Delete `TroubleshootingPanel`, `DiagnosticRow`, `recoverySteps`, the `NetworkDiagnostics` import and parameter. In `sessionStatusText`/`sessionDetailText` remove the `CaptivePending` cases.

- [ ] **Step 4: Handle actions in `MainActivity`**

```kotlin
    private fun onConfinementAction(action: ConfinementAction, kind: VpnKind) {
        when (action) {
            ConfinementAction.SIGN_IN_HERE -> viewModel.signInHere()
            ConfinementAction.OPEN_VPN_APP -> startActivity(VpnAppLauncher.resolve(this, kind).second)
            ConfinementAction.OPEN_NETWORK_SETTINGS -> startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
            ConfinementAction.SHARE_EVIDENCE -> Unit // handled by MainScreen's dialog
        }
    }
```

Compute `vpnAppLabel` once per state with `remember(confinement) { VpnAppLauncher.resolve(context, kind).first }` where `kind` is `(confinement as? Tunnelled)?.vpnKind ?: (confinement as? Blocked)?.vpnKind ?: VpnKind.NONE`.

- [ ] **Step 5: Build** — CI; locally if SDK present.

- [ ] **Step 6: Commit**

```bash
git add android/app/src/main
git commit -m "feat(android): one confinement card with a single action per state"
```

---

### Task 11: The system-chooser entry classifies before showing a WebView

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/CaptivePortalActivity.kt`

**Interfaces:**
- Consumes: `PortalProbe`, `classify`, `ConfinementStateText`, `ConfinementCard`, `VpnAppLauncher`, `VpnDetector`.

- [ ] **Step 1: Probe on the delivered network before `setContent`**

After the `bindProcessToNetwork(network)` call (line 85) add a classification pass. Use `lifecycleScope.launch` with `Dispatchers.IO`:

```kotlin
        lifecycleScope.launch {
            val bound = withContext(Dispatchers.IO) { probe.probe(network, testUrl = CONNECTIVITY_CHECK_URL) }
            val vpn = withContext(Dispatchers.IO) { VpnDetector.detect() }
            val strict = connectivityManager.getLinkProperties(network)?.privateDnsServerName != null
            val resolved = (bound as? ProbeResult.Portal)?.let { p ->
                val host = runCatching { URI(p.locationUrl).host }.getOrNull()
                if (host == null || host.all { it.isDigit() || it == '.' } || host.contains(':')) null
                else withContext(Dispatchers.IO) { runCatching { network.getAllByName(host).isNotEmpty() }.getOrDefault(false) }
            }
            val state = classify(ClassificationInputs(bound, null, vpn.interfaces, strict, resolved))
            Log.i(TAG, "System handoff confinement: ${state.schemaName}")
            // The system delivered a URL; prefer it over the probe's when confined.
            val url = if (state is ConfinementState.Confined) portalUrl else null
            render(state, url, network)
        }
```

Inject `probe: PortalProbe` with `@Inject lateinit var probe: PortalProbe` (provided by `AppModule`). Replace the existing `setContent { ... PortalScreen(...) }` with `render`:

```kotlin
    private fun render(state: ConfinementState, url: String?, network: Network) {
        setContent {
            GatepathTheme {
                if (url != null) {
                    PortalScreen(
                        portalUrl = url, network = network, connectivityManager = connectivityManager,
                        onDismiss = ::reportSignedIn, onBlockedNavigation = {}, onBlockedResource = {},
                        onTlsCertErrorBypassed = {}, onCertSummary = {},
                    )
                } else {
                    val kind = (state as? ConfinementState.Tunnelled)?.vpnKind
                        ?: (state as? ConfinementState.Blocked)?.vpnKind ?: VpnKind.NONE
                    val (label, launch) = VpnAppLauncher.resolve(this, kind)
                    ConfinementCard(
                        state = state, vpnAppLabel = label,
                        onAction = { action ->
                            when (action) {
                                ConfinementAction.OPEN_VPN_APP -> startActivity(launch)
                                ConfinementAction.OPEN_NETWORK_SETTINGS -> startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
                                else -> Unit
                            }
                        },
                        onShareEvidence = { /* no bundle on this entry; MainActivity owns sharing */ },
                    )
                }
            }
        }
    }
```

`onCertSummary` is the new `PortalScreen` parameter from Task 12; pass a no-op here. Remove the class KDoc sentences claiming the token "permits restricted-network access" (lines 30-34, 81-84).

- [ ] **Step 2: Build via CI; commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath/CaptivePortalActivity.kt
git commit -m "fix(android): system handoff shows the confinement state instead of a blank WebView"
```

---

### Task 12: Capture the certificate summary from the WebView

**Files:**
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/GatepathWebView.kt:310-338`
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/ui/PortalScreen.kt` (thread `onCertSummary`)
- Modify: `android/app/src/main/java/com/ventouxlabs/gatepath/MainActivity.kt` (`onCertSummary = viewModel::onCertSummary`)

**Interfaces:**
- Consumes: `CertSummary.of` (Task 4).
- Produces: `GatepathWebView(..., onCertSummary: (CertSummary) -> Unit, ...)`, same on `PortalScreen`.

- [ ] **Step 1: Build the summary in `onReceivedSslError`**

Before the `if (proceed)` branch:

```kotlin
        onCertSummary(summarise(error))
```

and the helper (top-level, private):

```kotlin
/**
 * Everything about the certificate that is safe to export: the error class,
 * validity window, whether it is self-signed, and a SHA-256 fingerprint. The
 * subject and issuer are gateway-authored text and stay on the device.
 */
private fun summarise(error: SslError): CertSummary {
    val cert = error.certificate
    val x509 = runCatching { cert.x509Certificate }.getOrNull()
    return CertSummary.of(
        primaryError = error.primaryError,
        notBefore = cert.validNotBeforeDate?.time,
        notAfter = cert.validNotAfterDate?.time,
        subjectEqualsIssuer = cert.issuedTo.dName == cert.issuedBy.dName,
        derEncoded = runCatching { x509?.encoded }.getOrNull(),
    )
}
```

`SslCertificate.getX509Certificate()` is API 29+, matching `minSdk = 29`.

- [ ] **Step 2: Thread the callback** through `buildWebViewClient`, `GatepathWebView`, `PortalScreen`, and `MainActivity` (`onCertSummary = viewModel::onCertSummary`).

- [ ] **Step 3: Build via CI; commit**

```bash
git add android/app/src/main/java/com/ventouxlabs/gatepath
git commit -m "feat(android): record a privacy-safe certificate summary in the incident evidence"
```

---

### Task 13: Move the test VPN into its own debug-only app

The e2e no-leak proof currently passes because Gatepath owns the VPN and therefore holds socket-protect rights. A second app owning the VPN is the production configuration.

**Files:**
- Create: `android/testvpn/build.gradle.kts`
- Create: `android/testvpn/src/main/AndroidManifest.xml`
- Move: `android/app/src/debug/java/com/ventouxlabs/gatepath/testvpn/GatepathTestVpnService.kt` → `android/testvpn/src/main/java/com/ventouxlabs/gatepath/testvpn/GatepathTestVpnService.kt`
- Move: `.../TestVpnControlActivity.kt` likewise
- Delete: `android/app/src/debug/AndroidManifest.xml`
- Modify: `android/settings.gradle.kts` (`include(":testvpn")`)
- Modify: `tests/e2e-android/guard/check_release_manifest.py`
- Modify: `.github/workflows/android-e2e.yml` (build both, guard both)

**Interfaces:**
- Produces: APK `android/testvpn/build/outputs/apk/debug/testvpn-debug.apk`, package `com.ventouxlabs.gatepath.testvpn`, sink at that package's `files/vpn-sink.jsonl`. Control extras unchanged (`gatepath.testvpn.action`, `gatepath.testvpn.label`) plus a new `gatepath.testvpn.mode` = `covering|excluding`.

- [ ] **Step 1: Module build file**

```kotlin
plugins {
    alias(libs.plugins.android.application)
}

android {
    namespace = "com.ventouxlabs.gatepath.testvpn"
    compileSdk = 37
    defaultConfig {
        applicationId = "com.ventouxlabs.gatepath.testvpn"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "e2e"
    }
    buildTypes {
        // Debug only by policy: release is never built or published (android-e2e guard).
        release { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }
}

dependencies {
    // org.json is in the platform; no dependencies.
}
```

`settings.gradle.kts`: add `include(":testvpn")`.

- [ ] **Step 2: Manifest**

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <!-- e2e-only: the no-leak sentinel VPN, owned by a package that is NOT Gatepath so
         netd treats Gatepath like any app under a third-party VPN. Never shipped. -->
    <application android:label="Gatepath test VPN" android:allowBackup="false">
        <service
            android:name=".GatepathTestVpnService"
            android:exported="false"
            android:permission="android.permission.BIND_VPN_SERVICE">
            <intent-filter>
                <action android:name="android.net.VpnService" />
            </intent-filter>
        </service>
        <activity
            android:name=".TestVpnControlActivity"
            android:exported="true"
            android:theme="@android:style/Theme.NoDisplay" />
    </application>
</manifest>
```

- [ ] **Step 3: Mode-aware service**

In `GatepathTestVpnService.startTun()` replace `.also { it.addAllowedApplication(packageName) }` with:

```kotlin
            .also { b ->
                when (mode) {
                    "covering" -> b.addAllowedApplication(GATEPATH_PACKAGE)   // third-party VPN covers Gatepath
                    "excluding" -> b.addDisallowedApplication(GATEPATH_PACKAGE) // Gatepath excluded (the product contract)
                    else -> error("unknown mode $mode")
                }
            }
```

with `private const val GATEPATH_PACKAGE = "com.ventouxlabs.gatepath"`, `mode` read from `intent.getStringExtra(EXTRA_MODE) ?: "covering"` in `onStartCommand` and forwarded by `TestVpnControlActivity` from extra `gatepath.testvpn.mode`. Remove the `BuildConfig.DEBUG` gate in the control activity (the whole app is test-only). Delete the `app/src/debug` copies and manifest.

There is no `owner` mode any more: the VPN app is now the test app, so Gatepath never holds protect rights. In `covering` mode the sink is a valid leak oracle (Gatepath's unbound traffic is captured); in `excluding` mode nothing of Gatepath's is covered, so the sink is not an oracle and the harness skips the markers (Task 14).

- [ ] **Step 4: Update the release guard**

`check_release_manifest.py` still asserts the app's release manifest lacks the markers; the positive control (debug manifest contains them) is no longer true. Change the positive control to read `android/testvpn/build/intermediates/**/debug/**/AndroidManifest.xml` and require the markers there; the CI job runs `./gradlew :app:processReleaseManifest :testvpn:processDebugManifest`. Update the usage docstring: `check_release_manifest.py <android dir>`.

- [ ] **Step 5: CI build**

In `android-e2e.yml` `assembleDebug` step: `./gradlew --no-daemon :app:assembleDebug :testvpn:assembleDebug`. In `release-vpn-guard`: `./gradlew --no-daemon :app:processReleaseManifest :testvpn:processDebugManifest` and `python3 tests/e2e-android/guard/check_release_manifest.py android`.

- [ ] **Step 6: Commit**

```bash
git add android/settings.gradle.kts android/testvpn android/app/src/debug tests/e2e-android/guard .github/workflows/android-e2e.yml
git commit -m "test(e2e-android): move the sentinel VPN into its own app so Gatepath is not the VPN owner"
```

---

### Task 14: Harness modes — `covering` and `excluding`

**Files:**
- Modify: `tests/e2e-android/scenario/run-scenario.py`
- Modify: `tests/e2e-android/scenario/ci-script.sh`
- Test: `tests/e2e-android/scenario/test_run_scenario_modes.py` (new)

**Interfaces:**
- Consumes: test VPN package `com.ventouxlabs.gatepath.testvpn` (Task 13), `files/confinement-state.txt` (Task 9).
- Produces: `--vpn-mode {covering,excluding}` (default `excluding`), `--testvpn-apk-path`; artefacts `confinement-state.txt`, `scenario-report.json` step `wait_confinement_state` with `data.state`; step lists `STEPS_COVERING` / `STEPS_EXCLUDING` exported for the assertions to check against.

- [ ] **Step 1: Write the failing unit test**

```python
"""Shape of the two scenario modes — checked without an emulator."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_scenario", Path(__file__).resolve().parent / "run-scenario.py"
)
rs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rs)  # type: ignore[union-attr]


def test_excluding_mode_signs_in_via_the_monitor_not_the_debug_intent():
    names = rs.step_names(rs.STEPS_EXCLUDING)
    assert "launch_debug_portal" not in names
    assert names.index("wait_confinement_state") < names.index("wait_portal_screen")
    assert "submit_login" in names and "wait_validated" in names


def test_covering_mode_never_reaches_the_portal_or_login():
    names = rs.step_names(rs.STEPS_COVERING)
    assert "submit_login" not in names
    assert "wait_validated" not in names
    assert "wait_confinement_state" in names
    assert "pull_confinement_state" in names


def test_both_modes_install_the_test_vpn_and_pull_the_sink():
    for steps in (rs.STEPS_COVERING, rs.STEPS_EXCLUDING):
        names = rs.step_names(steps)
        assert "install_testvpn" in names
        assert "pull_vpn_sink" in names
        assert "pull_audit_log" in names
```

Run: `cd tests/e2e-android && python -m pytest scenario/test_run_scenario_modes.py -q` — Expected: FAIL (`STEPS_EXCLUDING` missing).

- [ ] **Step 2: Constants and new steps**

Add near the top of `run-scenario.py`:

```python
TESTVPN_PACKAGE = "com.ventouxlabs.gatepath.testvpn"
TESTVPN_ACTIVITY = f"{TESTVPN_PACKAGE}/.TestVpnControlActivity"
VPN_SINK_RELATIVE = "files/vpn-sink.jsonl"          # now under TESTVPN_PACKAGE
# Written by MainViewModel's debug sink on every classification (Task 9).
CONFINEMENT_STATE_RELATIVE = "files/confinement-state.txt"
```

Change every `run-as {APP_PACKAGE} ... {VPN_SINK_RELATIVE}` to `run-as {TESTVPN_PACKAGE}` (in `_mark`, `_pull_sink`, `step_pull_vpn_sink`) and `step_grant_vpn` to `appops set {TESTVPN_PACKAGE} ACTIVATE_VPN allow`. `_testvpn()` appends `--es gatepath.testvpn.mode {state['vpn_mode']}` on `start`.

New steps:

```python
def step_install_testvpn(state: dict) -> dict:
    adb_helper.install_apk(state["serial"], state["testvpn_apk_path"])
    return {"apk_path": state["testvpn_apk_path"]}


def step_clear_confinement_state(state: dict) -> dict:
    """Delete the sidecar first so a stale file cannot read as this run's result."""
    adb_helper.shell(
        state["serial"], f"run-as {APP_PACKAGE} rm -f {CONFINEMENT_STATE_RELATIVE}", timeout=10, check=False
    )
    return {"cleared": CONFINEMENT_STATE_RELATIVE}


def step_launch_app(state: dict) -> dict:
    """Start MainActivity with NO debug extras: the monitor path must classify on its own."""
    serial = state["serial"]
    adb_helper.shell(serial, "logcat -G 8M", timeout=10, check=False)
    adb_helper.shell(serial, "logcat -c", timeout=10, check=False)
    out, err = adb_helper.shell_full(serial, f"am start -n {APP_PACKAGE}/.MainActivity", timeout=20, check=False)
    return {"am_output": (out or err).strip()[:200]}


def step_wait_confinement_state(state: dict) -> dict:
    """Poll the app-private sidecar the debug ViewModel writes; a file, not a log line."""
    serial = state["serial"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        got = adb_helper.shell(
            serial, f"run-as {APP_PACKAGE} cat {CONFINEMENT_STATE_RELATIVE}", timeout=10, check=False
        ).strip()
        if got:
            return {"state": got, "expected": state["expected_state"]}
        time.sleep(2)
    raise RuntimeError(f"{CONFINEMENT_STATE_RELATIVE} never appeared within 60s")


def step_pull_confinement_state(state: dict) -> dict:
    got = adb_helper.shell(
        state["serial"], f"run-as {APP_PACKAGE} cat {CONFINEMENT_STATE_RELATIVE}", timeout=10, check=False
    )
    (state["artifacts_dir"] / "confinement-state.txt").write_text(got)
    return {"bytes": len(got)}


def step_settle_covering(state: dict) -> dict:
    """Give a Tunnelled app time to misbehave. Nothing should reach the gateway."""
    time.sleep(20)
    _mark(state["serial"], "bound_end")
    return {"slept_sec": 20}
```

- [ ] **Step 3: Two step lists**

```python
COMMON_HEAD = [
    step("connect", step_connect), step("reset_settings", step_reset_settings),
    step("install", step_install), step("install_testvpn", step_install_testvpn),
    step("reset_gateway", step_reset_gateway), step("set_probe_urls", step_set_probe_urls),
    step("cycle_wifi", step_cycle_wifi), step("wait_for_captive", step_wait_for_captive),
    step("grant_vpn", step_grant_vpn), step("start_test_vpn", step_start_test_vpn),
    step("clear_confinement_state", step_clear_confinement_state),
]
COMMON_TAIL = [
    step("pull_vpn_sink", step_pull_vpn_sink), step("write_bundle", step_write_bundle),
    step("pull_bundle", step_pull_bundle), step("pull_logcat", step_pull_logcat),
    step("pull_audit_log", step_pull_audit_log), step("pull_confinement_state", step_pull_confinement_state),
    step("fetch_gateway_log", step_fetch_gateway_log), step("cleanup_settings", step_cleanup_settings),
    step("disconnect", step_disconnect),
]
# Third-party VPN covers Gatepath: EPERM on the bound probe → TUNNELLED, no session.
STEPS_COVERING = COMMON_HEAD + [
    step("liveness_probe", step_liveness_probe), step("launch_app", step_launch_app),
    step("wait_confinement_state", step_wait_confinement_state), step("settle_covering", step_settle_covering),
] + COMMON_TAIL
# Gatepath excluded from the VPN: CONFINED, monitor opens the session, sign-in completes.
STEPS_EXCLUDING = COMMON_HEAD + [
    step("launch_app", step_launch_app), step("wait_confinement_state", step_wait_confinement_state),
    step("wait_portal_screen", step_wait_portal_screen), step("submit_login", step_submit_login),
    step("wait_validated", step_wait_validated), step("mark_bound_end", step_mark_bound_end),
] + COMMON_TAIL


def step_names(steps) -> list[str]:
    return [s.step_name for s in steps]
```

Make `step()` set `runner.step_name = name` before returning. `wait_portal_screen` in excluding mode must not require the debug-intent log line: guard the `accepted` check with `if state["vpn_mode"] == "excluding": accepted = True`. `liveness_probe` lays `bound_begin`; in excluding mode nothing is covered, so skip the sink markers there (the sink is not an oracle in that mode) and do not call `_mark`.

`parse_args`: add `--testvpn-apk-path` (required), `--vpn-mode` (`choices=("covering", "excluding")`, default `excluding`). `main()`: `state["vpn_mode"]`, `state["expected_state"] = {"covering": "tunnelled", "excluding": "confined"}[args.vpn_mode]`, choose `STEPS_COVERING` or `STEPS_EXCLUDING`. Teardown stops the VPN via `TESTVPN_ACTIVITY`.

`ci-script.sh`: pass `--testvpn-apk-path android/testvpn/build/outputs/apk/debug/testvpn-debug.apk --vpn-mode "${GATEPATH_E2E_VPN_MODE:-excluding}"` and `--artifacts-dir tests/e2e-android/artifacts/${GATEPATH_E2E_VPN_MODE:-excluding}`.

- [ ] **Step 4: Run** `cd tests/e2e-android && python -m pytest scenario -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e-android/scenario
git commit -m "test(e2e-android): covering and excluding VPN modes drive the monitor path"
```

---

### Task 15: Assertions for the two modes and the CI matrix

**Files:**
- Modify: `tests/e2e-android/driver/assertions.py`
- Modify: `tests/e2e-android/driver/test_assertions.py`
- Modify: `.github/workflows/android-e2e.yml`

**Interfaces:**
- Consumes: artefacts `confinement-state.txt`, `scenario-report.json`, `audit_log.jsonl`, `gateway-log.json`, `vpn-sink.jsonl`, `logcat.txt`.
- Produces: `assertions.py <artifacts-dir> --vpn-mode {covering,excluding}`; new checks `G. Confinement state` and mode-specific variants of B, C, D.

- [ ] **Step 1: Write the failing assertion tests**

Append to `test_assertions.py`:

```python
def test_covering_mode_requires_tunnelled_and_no_portal_hit():
    failures: list[str] = []
    assertions.check_confinement("tunnelled\n", "covering", failures)
    assert not failures
    assertions.check_confinement("confined\n", "covering", failures)
    assert failures and "confinement" in failures[-1]


def test_covering_mode_fails_on_any_android_portal_hit():
    failures: list[str] = []
    assertions.check_gateway_silent([GW_PORTAL], failures)
    assert failures
    failures.clear()
    assertions.check_gateway_silent([], failures)
    assert not failures


def test_excluding_mode_requires_confined_audit_entry():
    failures: list[str] = []
    assertions.check_audit_confined([{"close_reason": "portal_completed", "confinement": "confined"}], failures)
    assert not failures
    assertions.check_audit_confined([{"close_reason": "portal_completed", "confinement": "unconfined"}], failures)
    assert failures


def test_empty_state_file_is_a_failure_not_a_pass():
    failures: list[str] = []
    assertions.check_confinement("", "excluding", failures)
    assert failures
```

Run: `cd tests/e2e-android && python -m pytest driver -q` — Expected: FAIL (`check_confinement` missing).

- [ ] **Step 2: Implement the checks**

```python
def check_confinement(text: str, mode: str, failures: list[str]) -> None:
    """G. The state the app classified, read from the pulled sidecar.

    Absent evidence is a failure: an empty file means the ViewModel never
    classified, which is the silent short-circuit this harness exists to catch.
    """
    print("G. Confinement state")
    expected = {"covering": "tunnelled", "excluding": "confined"}[mode]
    got = text.strip()
    if not got:
        fail("confinement.file", "confinement-state.txt missing or empty — nothing was classified", failures)
    elif got != expected:
        fail("confinement.state", f"expected {expected!r} in {mode} mode, app classified {got!r}", failures)
    else:
        ok("confinement.state", got)


def check_gateway_silent(entries: list[dict[str, Any]], failures: list[str]) -> None:
    """C' (covering). A Tunnelled app must never load the portal: fail on any /portal hit from an Android UA."""
    print("C'. Gateway must be silent")
    hits = [
        e for e in entries
        if str(e.get("path", "")).startswith("/portal")
        and "Android" in (e.get("headers") or {}).get("User-Agent", "")
    ]
    if hits:
        fail("gateway.silent", f"{len(hits)} /portal hit(s) from an Android UA while Tunnelled — a WebView opened", failures)
    else:
        ok("gateway.silent", "no /portal request from an Android UA")


def check_audit_confined(entries: list[dict[str, Any]], failures: list[str]) -> None:
    """B' (excluding). The completed session must carry confinement=confined (schema v2)."""
    print("B'. Audit confinement")
    completed = [e for e in entries if e.get("close_reason") == "portal_completed"]
    if not completed:
        fail("audit.confined", "no portal_completed entry", failures)
        return
    bad = [e.get("confinement") for e in completed if e.get("confinement") != "confined"]
    if bad:
        fail("audit.confined", f"portal_completed entries with confinement={bad}", failures)
    else:
        ok("audit.confined", f"{len(completed)} entry/entries confined")
```

`main()` gains `--vpn-mode` (argparse; keep the positional artifacts dir). Mode `covering`: run A (with `EXPECTED_STEPS` swapped for the covering list, imported from the scenario module by name), G, C', D (sink: D1 liveness required; D2 window from `bound_begin` to `bound_end` must be silent — it will be, since EPERM'd connects emit nothing), and assert the audit log has **no** `portal_completed` entry. Mode `excluding`: A, G, B, B', C, E, F. Both modes: `check_diagnostics_bundle` additionally requires the string `confinement: ` in the bundle.

- [ ] **Step 3: CI matrix**

In `android-e2e.yml` `emulator-e2e` job add:

```yaml
    strategy:
      fail-fast: false
      matrix:
        vpn_mode: [covering, excluding]
    name: Android emulator E2E (${{ matrix.vpn_mode }})
    env:
      GATEPATH_E2E_VPN_MODE: ${{ matrix.vpn_mode }}
```

Assertions step: `python3 driver/assertions.py artifacts/${{ matrix.vpn_mode }} --vpn-mode ${{ matrix.vpn_mode }}`. Artifact upload name: `e2e-android-artifacts-${{ matrix.vpn_mode }}`.

- [ ] **Step 4: Run** `cd tests/e2e-android && python -m pytest driver scenario -q` — Expected: PASS. The emulator run itself is CI-only (needs `/dev/kvm`).

- [ ] **Step 5: Commit**

```bash
git add tests/e2e-android/driver .github/workflows/android-e2e.yml
git commit -m "test(e2e-android): assert the classified state per VPN mode"
```

---

### Task 16: Docs, changelog and the physical checklist

**Files:**
- Modify: `docs/SECURITY_MODEL.md` ("Android-specific guarantees" section and threat-model rows)
- Modify: `docs/RATIONALE.md:136-152`
- Modify: `README.md:35`
- Modify: `docs/TESTING_ANDROID.md`
- Modify: `docs/ROADMAP.md` (P0.1 status)
- Modify: `tests/e2e-android/HARNESS_NOTES.md` (No-leak sentinel section)
- Modify: `CHANGELOG.md` (Unreleased)

- [ ] **Step 1: SECURITY_MODEL.md**

Replace the first bullet of "Android-specific guarantees" with:

```markdown
- Portal-session traffic is bound to the captive `Network` via
  `bindProcessToNetwork()`, **and the app verifies on every incident that the
  binding is honoured.** netd refuses explicit network selection for a UID
  covered by a secure (non-bypassable) VPN unless the UID can protect sockets
  (`NetworkController::checkUserNetworkAccessLocked`), so under Tailscale,
  TorGuard or WireGuard the bound probe fails with `EPERM`. Gatepath then
  reports **Tunnelled** and does not open a sign-in WebView. The product
  contract is therefore: **exclude Gatepath in your VPN client's app
  split-tunnelling.** Only then does the Wi-Fi-bound probe reach the gateway
  (**Confined**) and in-app sign-in is offered. The state is classified by
  `ConfinementState.classify` and recorded in the evidence bundle; every
  audit entry carries `confinement`.

  Costs, stated plainly: an excluded Gatepath is outside the VPN permanently,
  so its connectivity probe and the diagnostic DoH query leave in the clear
  over the default network at all times. Gatepath cannot bypass strict
  Private DNS (that needs `NETWORK_BYPASS_PRIVATE_DNS`), so a hostname portal
  under strict mode is reported as **DnsStrict** with the instruction to use
  the system handler or set Private DNS to Automatic for the sign-in.
```

Add a row to the threat-model table: "Secure VPN covering Gatepath (not excluded) | **Fail-closed**: bound sockets get `EPERM`, no sign-in, no leak; user is told to exclude Gatepath". Remove every "restricted" claim about captive networks in this file.

- [ ] **Step 2: RATIONALE.md** — in "Relationship to the Android model" change "Android achieves the same goal with `bindProcessToNetwork()`" to "Android achieves the same goal with `bindProcessToNetwork()` **only when no secure VPN covers Gatepath**, which in practice means excluding Gatepath in the VPN client; the app verifies this per incident (see SECURITY_MODEL.md)."

- [ ] **Step 3: README.md** — row "Bind portal traffic to WiFi interface": Android column becomes `Yes, when excluded from the VPN; verified per incident`.

- [ ] **Step 4: TESTING_ANDROID.md** — add a section "Physical confinement matrix" with a table: rows Tailscale tailnet-only / Tailscale exit node / TorGuard, columns Gatepath included / excluded, cells `Tunnelled` / `Confined`; a second table Private DNS strict / automatic × hostname portal / IP-literal portal with `DnsStrict` only in strict×hostname; instructions to read `files/confinement-state.txt` via `adb shell run-as` on a debug build and to share the bundle. Note the `covering`/`excluding` harness modes.

- [ ] **Step 5: ROADMAP.md P0.1 and HARNESS_NOTES.md** — add to P0.1: "The original proof ran with Gatepath owning the test VPN, which grants protect rights; `covering` mode (2026-09) reruns it with a separate VPN owner and asserts fail-closed `Tunnelled`; `excluding` mode proves the product contract end-to-end." Replace the HARNESS_NOTES "No-leak sentinel" section with the two-mode description and the `com.ventouxlabs.gatepath.testvpn` package.

- [ ] **Step 6: CHANGELOG.md Unreleased**

```markdown
### Changed
- **Android:** captive incidents are classified into a confinement state
  (Confined / Tunnelled / Blocked / DnsStrict / Unknown). In-app sign-in is
  offered only when the Wi-Fi binding is verified; otherwise the app says
  exactly why and what to do. The `CaptivePending` troubleshooting list and
  the incorrect "restricted network" explanation are gone.
- **Audit log schema v2:** `blocked_*` counters renamed to `observed_*`; new
  required `confinement` field on both platforms. Readers accept v1 lines.
### Added
- **Android:** incident evidence in the diagnostics bundle (probe path,
  resolver comparison, certificate summary, confinement).
- **e2e-android:** `covering` and `excluding` VPN modes with a separate
  test-VPN app, so the harness exercises a VPN Gatepath does not own.
```

- [ ] **Step 7: Commit**

```bash
git add docs README.md CHANGELOG.md tests/e2e-android/HARNESS_NOTES.md
git commit -m "docs: state the Android confinement contract and schema v2"
```

---

## Self-review notes

- Spec §3 `Confined` carries the capture: implemented in Task 2. §4 fields: `IncidentEvidence` has every listed field (Task 4); `resolver_wifi`/`resolver_doh` filled in Task 9. §5 UI actions: Task 10; `signInHere` exists for Confined after a dismiss. §6 schema: Tasks 5–7. §7 docs: Task 16. §8 tests: Tasks 2–4 (JVM), 14–15 (emulator), 16 (physical). §9/§10 honoured.
- The desktop `Confinement` enum and the Android `ConfinementState.schemaName` share the wire vocabulary only for `confined`; the audit `confinement_enum` is deliberately `confined|unconfined`, while the evidence bundle uses the full five-state vocabulary. Do not add the five states to the audit enum.
- Kotlin `PortalSession` counters keep the `blocked*` property names; only serialized keys change (Task 5, Step 5). This is intentional scope control.
