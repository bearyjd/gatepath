# Android No-Leak Sentinel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, at the network level, that the Gatepath Android portal WebView's traffic cannot leak off the captive WiFi `Network` — by extending the existing emulator E2E with a debug-only `VpnService` leak detector.

**Architecture:** A debug-only `VpnService` becomes the system default network and records the destination of every packet the Gatepath app emits while *not* bound to the captive `Network`. A process bound via `bindProcessToNetwork(wifi)` bypasses the VPN, so the VPN's TUN is a leak detector: an unbound liveness probe must appear in the sink (proving it intercepts), and the bound portal session must leave the sink silent (proving confinement). Phases are delimited by marker lines the service writes into the sink, so no cross-machine clock comparison is needed.

**Tech Stack:** Kotlin (Android, minSdk 29 / compile+target 35, JVM 21), Android `VpnService`, the existing Python 3.12 scenario harness (`tests/e2e-android`), GitHub Actions `reactivecircus/android-emulator-runner` (API 34 google_apis).

## Global Constraints

- **Package:** `cc.grepon.gatepath`. All Kotlin in package `cc.grepon.gatepath.testvpn`.
- **All test apparatus lives in `android/app/src/debug/` only.** Production source set (`src/main/`) gets ZERO changes. Release builds must contain no VpnService and no `BIND_VPN_SERVICE`.
- **No new `<uses-permission>`** anywhere. The VpnService is declared with `android:permission="android.permission.BIND_VPN_SERVICE"` on the `<service>` (system-held), in the **debug** manifest only.
- **Sentinel:** UDP to `203.0.113.7:9` (TEST-NET-3, RFC 5737 — never a real host). **Portal host:** `10.0.2.2:18080`.
- **Sink file:** `files/vpn-sink.jsonl` in the app's `filesDir`, pulled host-side via `run-as cc.grepon.gatepath cat files/vpn-sink.jsonl` (debuggable debug build).
- **Confinement proof order:** D1 liveness gate first (sentinel packet before `bound_begin`); only then D2 confinement (bound window silent). A missing/empty sink or missing markers is a hard FAIL, never a skip.
- **Bound window** is delimited by `{"marker":"bound_begin"}` / `{"marker":"bound_end"}` lines in the sink (append-order, not timestamps).
- **Open captive networks only** — unchanged project limitation.
- Kotlin style: `val` over `var`, no `!!`, official Kotlin style.

---

### Task 1: Feasibility spike — VPN-as-default headless (DECISION GATE)

De-risk the one unproven assumption before building everything: that `appops set … ACTIVATE_VPN allow` suppresses the consent dialog and a minimal `VpnService` can `establish()` headless and capture an unbound packet on this exact emulator image. This task's code is the seed of Task 2, so it is not throwaway — but its **outcome gates the rest of the plan**.

**Files:**
- Create: `android/app/src/debug/java/cc/grepon/gatepath/testvpn/GatepathTestVpnService.kt` (minimal version; Task 2 completes it)
- Create: `android/app/src/debug/java/cc/grepon/gatepath/testvpn/TestVpnControlActivity.kt` (minimal: start + probe)
- Create: `android/app/src/debug/AndroidManifest.xml`

**Interfaces:**
- Produces: `GatepathTestVpnService` with `companion` consts `ACTION_START`, `ACTION_STOP`, `SINK_FILE = "vpn-sink.jsonl"`; `TestVpnControlActivity` reachable via `am start -n cc.grepon.gatepath/.testvpn.TestVpnControlActivity --es gatepath.testvpn.action <start|probe|stop>`.

- [ ] **Step 1: Minimal service** — create `GatepathTestVpnService.kt`:

```kotlin
package cc.grepon.gatepath.testvpn

import android.content.Intent
import android.net.VpnService
import android.os.ParcelFileDescriptor
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream

/**
 * DEBUG-ONLY local VpnService used by the android-e2e no-leak sentinel
 * (ROADMAP P0.1). Becomes the system default network and records the
 * destination of every IPv4 packet the Gatepath app emits while unbound,
 * to files/vpn-sink.jsonl. Never forwards (a black hole). Absent from
 * release builds — lives in src/debug/.
 */
class GatepathTestVpnService : VpnService() {

    @Volatile private var running = false
    private var tun: ParcelFileDescriptor? = null
    private val sinkLock = Any()

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { teardown(); stopSelf(); return START_NOT_STICKY }
            else -> startTun()
        }
        return START_STICKY
    }

    private fun append(line: String) {
        synchronized(sinkLock) { File(filesDir, SINK_FILE).appendText(line + "\n") }
    }

    private fun startTun() {
        if (running) return
        File(filesDir, SINK_FILE).writeText("")  // fresh per run
        val pfd = Builder()
            .setSession("gatepath-test-sink")
            .addAddress(TUN_ADDR, 32)
            .addRoute("0.0.0.0", 0)
            .setMtu(MTU)
            .also { it.addAllowedApplication(packageName) }
            .establish() ?: run { Log.e(TAG, "establish() null — VPN not authorized?"); return }
        tun = pfd
        running = true
        Thread { readLoop(FileInputStream(pfd.fileDescriptor)) }.apply { isDaemon = true }.start()
        Log.i(TAG, "test VPN sink established")
    }

    private fun readLoop(input: FileInputStream) {
        val buf = ByteArray(MTU)
        while (running) {
            val n = try { input.read(buf) } catch (e: Exception) { break }
            if (n <= 0) continue
            parseIpv4(buf, n)?.let { append(it) }
        }
    }

    private fun parseIpv4(pkt: ByteArray, len: Int): String? {
        if (len < 20 || ((pkt[0].toInt() ushr 4) and 0xF) != 4) return null
        val ihl = (pkt[0].toInt() and 0xF) * 4
        if (len < ihl) return null
        val proto = pkt[9].toInt() and 0xFF
        val dst = "${pkt[16].toInt() and 0xFF}.${pkt[17].toInt() and 0xFF}." +
                  "${pkt[18].toInt() and 0xFF}.${pkt[19].toInt() and 0xFF}"
        val dport = if ((proto == 6 || proto == 17) && len >= ihl + 4)
            ((pkt[ihl + 2].toInt() and 0xFF) shl 8) or (pkt[ihl + 3].toInt() and 0xFF) else -1
        return JSONObject()
            .put("dst", dst).put("port", dport)
            .put("proto", when (proto) { 6 -> "TCP"; 17 -> "UDP"; else -> "IP$proto" })
            .put("t", System.currentTimeMillis() / 1000.0)
            .toString()
    }

    private fun teardown() {
        running = false
        try { tun?.close() } catch (_: Exception) {}
        tun = null
    }

    override fun onDestroy() { teardown(); super.onDestroy() }

    companion object {
        private const val TAG = "GatepathTestVpn"
        const val ACTION_START = "cc.grepon.gatepath.testvpn.START"
        const val ACTION_STOP = "cc.grepon.gatepath.testvpn.STOP"
        const val SINK_FILE = "vpn-sink.jsonl"
        private const val TUN_ADDR = "10.111.0.2"
        private const val MTU = 1500
    }
}
```

- [ ] **Step 2: Minimal control activity** — create `TestVpnControlActivity.kt`:

```kotlin
package cc.grepon.gatepath.testvpn

import android.app.Activity
import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import android.util.Log
import cc.grepon.gatepath.BuildConfig
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/** DEBUG-ONLY harness control surface, driven by `am start … --es gatepath.testvpn.action <a>`. */
class TestVpnControlActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (BuildConfig.DEBUG) handle(intent)
        finish()
    }

    private fun handle(intent: Intent) {
        when (intent.getStringExtra(EXTRA_ACTION)) {
            "start" -> {
                if (VpnService.prepare(this) != null) { Log.e(TAG, "VPN not authorized"); return }
                startService(svc(GatepathTestVpnService.ACTION_START))
            }
            "probe" -> sendUnboundProbe()
            "stop" -> startService(svc(GatepathTestVpnService.ACTION_STOP))
            else -> Log.w(TAG, "unknown action")
        }
    }

    private fun svc(action: String) =
        Intent(this, GatepathTestVpnService::class.java).setAction(action)

    private fun sendUnboundProbe() {
        // Off the main thread: DatagramSocket.send is network I/O and would throw
        // NetworkOnMainThreadException in onCreate. join() so the activity doesn't
        // finish before the datagrams are flushed to the (VPN) default route.
        val addr = InetAddress.getByName(SENTINEL_IP)
        Thread {
            DatagramSocket().use { sock ->
                repeat(PROBE_COUNT) {
                    val p = "gatepath-liveness".toByteArray()
                    sock.send(DatagramPacket(p, p.size, addr, SENTINEL_PORT))
                }
            }
        }.apply { start(); join() }
        Log.i(TAG, "sent $PROBE_COUNT datagrams to $SENTINEL_IP:$SENTINEL_PORT")
    }

    companion object {
        private const val TAG = "GatepathTestVpnCtl"
        const val EXTRA_ACTION = "gatepath.testvpn.action"
        const val SENTINEL_IP = "203.0.113.7"
        const val SENTINEL_PORT = 9
        const val PROBE_COUNT = 3
    }
}
```

- [ ] **Step 3: Debug manifest** — create `android/app/src/debug/AndroidManifest.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <!-- DEBUG-ONLY: no-leak sentinel test VPN (ROADMAP P0.1). Merged into debug builds only. -->
    <application>
        <service
            android:name=".testvpn.GatepathTestVpnService"
            android:exported="false"
            android:permission="android.permission.BIND_VPN_SERVICE">
            <intent-filter>
                <action android:name="android.net.VpnService" />
            </intent-filter>
        </service>
        <activity
            android:name=".testvpn.TestVpnControlActivity"
            android:exported="true"
            android:theme="@android:style/Theme.NoDisplay" />
    </application>
</manifest>
```

- [ ] **Step 4: Compile the debug build**

Run: `cd android && ./gradlew --no-daemon :app:assembleDebug`
Expected: `BUILD SUCCESSFUL`. (If `org.json` is unresolved it is part of the Android SDK — no dependency needed.)

- [ ] **Step 5: Run the spike on an emulator** (locally with an API-34 google_apis AVD, or via a scratch `workflow_dispatch`):

```bash
# with an emulator booted and the debug APK installed:
adb shell appops set cc.grepon.gatepath ACTIVATE_VPN allow
adb shell am start -n cc.grepon.gatepath/.testvpn.TestVpnControlActivity --es gatepath.testvpn.action start
sleep 3
adb shell am start -n cc.grepon.gatepath/.testvpn.TestVpnControlActivity --es gatepath.testvpn.action probe
sleep 2
adb shell run-as cc.grepon.gatepath cat files/vpn-sink.jsonl
adb shell am start -n cc.grepon.gatepath/.testvpn.TestVpnControlActivity --es gatepath.testvpn.action stop
```

Expected: the `cat` shows ≥1 line with `"dst":"203.0.113.7"`, `"proto":"UDP"`.

- [ ] **Step 6: DECISION GATE**

- **Green** (sentinel packet captured) → proceed to Task 2.
- **Red** (establish() null, or no packet captured) → STOP. The VPN-as-default-headless path is infeasible on this image; switch to the fallback in the spec (an `androidTest` instrumentation test of the per-`Network` binding API). Record the failure mode in the spec's "Open risks" and re-plan. Do not continue Tasks 2–8 as written.

- [ ] **Step 7: Commit**

```bash
git add android/app/src/debug
git commit -m "test(android-e2e): spike a debug VpnService leak-detector for the no-leak sentinel (P0.1)"
```

---

### Task 2: Complete the VpnService sink (markers)

Add the phase markers the assertion buckets on. The service must accept an `ACTION_MARK` carrying a label and append a `{"marker":<label>}` line in the same append-ordered file.

**Files:**
- Modify: `android/app/src/debug/java/cc/grepon/gatepath/testvpn/GatepathTestVpnService.kt`

**Interfaces:**
- Consumes: the Task 1 service.
- Produces: `GatepathTestVpnService.ACTION_MARK` (String) + `EXTRA_LABEL` (String); the service appends `{"marker":"<label>","t":<float>}` lines into `vpn-sink.jsonl`.

- [ ] **Step 1: Add the MARK action + constants**

In the `companion object`, add below `ACTION_STOP`:

```kotlin
        const val ACTION_MARK = "cc.grepon.gatepath.testvpn.MARK"
        const val EXTRA_LABEL = "cc.grepon.gatepath.testvpn.label"
```

In `onStartCommand`, change the `when` to handle MARK:

```kotlin
        when (intent?.action) {
            ACTION_STOP -> { teardown(); stopSelf(); return START_NOT_STICKY }
            ACTION_MARK -> {
                val label = intent.getStringExtra(EXTRA_LABEL) ?: "?"
                append(JSONObject().put("marker", label)
                    .put("t", System.currentTimeMillis() / 1000.0).toString())
                return START_STICKY
            }
            else -> startTun()
        }
```

- [ ] **Step 2: Forward MARK from the control activity**

In `TestVpnControlActivity.handle`, add a `mark` branch and the label extra. Replace the `when` block with:

```kotlin
        when (intent.getStringExtra(EXTRA_ACTION)) {
            "start" -> {
                if (VpnService.prepare(this) != null) { Log.e(TAG, "VPN not authorized"); return }
                startService(svc(GatepathTestVpnService.ACTION_START))
            }
            "probe" -> sendUnboundProbe()
            "mark" -> startService(
                svc(GatepathTestVpnService.ACTION_MARK).putExtra(
                    GatepathTestVpnService.EXTRA_LABEL,
                    intent.getStringExtra(EXTRA_LABEL) ?: "?"))
            "stop" -> startService(svc(GatepathTestVpnService.ACTION_STOP))
            else -> Log.w(TAG, "unknown action")
        }
```

Add the extra-key constant to `TestVpnControlActivity.companion`:

```kotlin
        const val EXTRA_LABEL = "gatepath.testvpn.label"
```

- [ ] **Step 3: Compile**

Run: `cd android && ./gradlew --no-daemon :app:assembleDebug`
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 4: Commit**

```bash
git add android/app/src/debug
git commit -m "test(android-e2e): add phase markers to the test VPN sink"
```

---

### Task 3: `check_vpn_confinement` assertion (TDD)

The pure, unit-testable heart of the proof. Write the test first.

**Files:**
- Create: `tests/e2e-android/driver/test_assertions.py`
- Modify: `tests/e2e-android/driver/assertions.py`

**Interfaces:**
- Consumes: a list of parsed sink dicts (packet lines `{"dst","port","proto","t"}` and marker lines `{"marker","t"}`), in file order.
- Produces: `check_vpn_confinement(lines: list[dict], failures: list[str]) -> None` and a `SENTINEL_IP` constant; `main()` reads `vpn-sink.jsonl` and calls it; `EXPECTED_STEPS` gains the new step names.

- [ ] **Step 1: Write the failing test** — create `tests/e2e-android/driver/test_assertions.py`:

```python
"""Unit tests for the VPN-sink no-leak assertion."""
from __future__ import annotations

import assertions

BEGIN = {"marker": "bound_begin", "t": 2.0}
END = {"marker": "bound_end", "t": 9.0}
SENTINEL = {"dst": "203.0.113.7", "port": 9, "proto": "UDP", "t": 1.0}
PORTAL_LEAK = {"dst": "10.0.2.2", "port": 18080, "proto": "TCP", "t": 5.0}


def test_confined_passes():
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL, BEGIN, END], failures)
    assert failures == []


def test_leak_fails_and_names_dst():
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL, BEGIN, PORTAL_LEAK, END], failures)
    assert any("LEAK" in f and "10.0.2.2" in f for f in failures)


def test_missing_liveness_is_vacuous_fail():
    failures: list[str] = []
    assertions.check_vpn_confinement([BEGIN, END], failures)
    assert any("liveness" in f for f in failures)


def test_missing_markers_fails():
    failures: list[str] = []
    assertions.check_vpn_confinement([SENTINEL], failures)
    assert any("marker" in f for f in failures)
```

- [ ] **Step 2: Run it — verify it fails**

Run: `cd tests/e2e-android/driver && python3 -m pytest test_assertions.py -v`
Expected: FAIL — `AttributeError: module 'assertions' has no attribute 'check_vpn_confinement'`.

- [ ] **Step 3: Implement `check_vpn_confinement`** — in `tests/e2e-android/driver/assertions.py`, add the constant near `OFF_DOMAIN_HOSTNAMES`:

```python
SENTINEL_IP = "203.0.113.7"
```

Add the function after `check_gateway_log`:

```python
def check_vpn_confinement(lines: list[dict[str, Any]], failures: list[str]) -> None:
    """D. The network-level no-leak proof over the VPN sink (ROADMAP P0.1).

    The bound window is delimited by 'bound_begin'/'bound_end' marker lines the
    test VpnService wrote into the sink (append-order, so no host/device clock
    comparison is needed). D1 (liveness) must hold before D2 (confinement) means
    anything: if the sink never saw the unbound probe it is not intercepting the
    default route, and a silent bound window is vacuous.
    """
    print("D. VPN sink (no-leak confinement)")
    begin = next((i for i, e in enumerate(lines) if e.get("marker") == "bound_begin"), None)
    end = next((i for i, e in enumerate(lines) if e.get("marker") == "bound_end"), None)
    if begin is None or end is None:
        fail("vpn.markers", f"missing bound-window markers (begin={begin}, end={end})", failures)
        return
    if end < begin:
        fail("vpn.markers", f"bound_end ({end}) precedes bound_begin ({begin})", failures)
        return

    # D1 — liveness gate: a sentinel packet must appear BEFORE bound_begin.
    pre = [e for e in lines[:begin] if e.get("dst") == SENTINEL_IP]
    if not pre:
        fail(
            "vpn.liveness",
            "the VPN sink never captured the unbound probe to the sentinel — the "
            "sink is not intercepting the default route, so a silent bound window "
            "proves nothing",
            failures,
        )
        return
    ok("vpn.liveness", f"{len(pre)} unbound sentinel packet(s) captured")

    # D2 — confinement: the bound window must be packet-silent.
    leaks = [e for e in lines[begin + 1:end] if "dst" in e]
    if leaks:
        s = leaks[0]
        fail(
            "vpn.confinement",
            f"LEAK: bound-phase Gatepath traffic to {s.get('dst')}:{s.get('port')} "
            f"escaped onto the default (VPN) network ({len(leaks)} packet(s))",
            failures,
        )
    else:
        ok("vpn.confinement", "bound window silent — traffic confined to WiFi")
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `cd tests/e2e-android/driver && python3 -m pytest test_assertions.py -v`
Expected: 4 passed.

- [ ] **Step 5: Wire it into `main()` + `EXPECTED_STEPS`** — in `assertions.py`, extend `EXPECTED_STEPS` (insert after `"wait_for_captive",`):

```python
    "grant_vpn",
    "start_test_vpn",
    "liveness_probe",
```

and (insert after `"wait_validated",`):

```python
    "mark_bound_end",
    "pull_vpn_sink",
```

In `main()`, after the `gateway_path` block, add:

```python
    sink_path = root / "vpn-sink.jsonl"
    if not sink_path.exists() or sink_path.stat().st_size == 0:
        failures.append("vpn.file: vpn-sink.jsonl missing or empty")
        print(f"  ✗ vpn-sink.jsonl missing or empty in {root}", file=sys.stderr)
    else:
        sink_lines = [
            json.loads(line)
            for line in sink_path.read_text().splitlines()
            if line.strip()
        ]
        check_vpn_confinement(sink_lines, failures)
```

- [ ] **Step 6: Re-run the unit test (regression) + commit**

Run: `cd tests/e2e-android/driver && python3 -m pytest test_assertions.py -v`
Expected: 4 passed.

```bash
git add tests/e2e-android/driver/assertions.py tests/e2e-android/driver/test_assertions.py
git commit -m "test(android-e2e): VPN-sink no-leak assertion + unit tests (P0.1)"
```

---

### Task 4: Harness steps in `run-scenario.py`

Wire the VPN lifecycle into the scenario, and guarantee teardown even on mid-scenario failure.

**Files:**
- Modify: `tests/e2e-android/scenario/run-scenario.py`

**Interfaces:**
- Consumes: `GatepathTestVpnService` actions and `TestVpnControlActivity` (Tasks 1–2); the existing `step()`, `STEPS`, `state`, `adb_helper` API.
- Produces: scenario steps `grant_vpn`, `start_test_vpn`, `liveness_probe`, `mark_bound_end`, `pull_vpn_sink`; artifact `vpn-sink.jsonl`.

- [ ] **Step 1: Add module constants** — after `APP_PACKAGE = "cc.grepon.gatepath"`:

```python
TESTVPN_ACTIVITY = f"{APP_PACKAGE}/.testvpn.TestVpnControlActivity"
VPN_SINK_RELATIVE = "files/vpn-sink.jsonl"
SENTINEL_IP = "203.0.113.7"


def _testvpn(serial: str, action: str, label: str | None = None) -> None:
    cmd = f"am start -n {TESTVPN_ACTIVITY} --es gatepath.testvpn.action {action}"
    if label:
        cmd += f" --es gatepath.testvpn.label {label}"
    adb_helper.shell(serial, cmd, timeout=20, check=False)
```

- [ ] **Step 2: Add the step functions** — after `step_wait_for_captive`:

```python
def step_grant_vpn(state: dict) -> dict:
    """Pre-authorize the VpnService so establish() needs no consent dialog.
    appops runs as the adb shell uid — no root required on the emulator."""
    adb_helper.shell(
        state["serial"], f"appops set {APP_PACKAGE} ACTIVATE_VPN allow", timeout=10
    )
    return {"granted": "ACTIVATE_VPN"}


def step_start_test_vpn(state: dict) -> dict:
    """Bring up the debug leak-detector VPN as the system default network."""
    _testvpn(state["serial"], "start")
    time.sleep(3)  # let establish() bring up the TUN before probing
    return {"started": True}


def step_liveness_probe(state: dict) -> dict:
    """Unbound UDP burst to the sentinel (must hit the VPN sink), then a settle
    and the bound_begin marker. The settle keeps any late probe packet from
    landing inside the bound window."""
    serial = state["serial"]
    _testvpn(serial, "probe")
    time.sleep(2)  # quiescence settle before the bound window opens
    _testvpn(serial, "mark", label="bound_begin")
    return {"sentinel": SENTINEL_IP, "marked": "bound_begin"}


def step_mark_bound_end(state: dict) -> dict:
    """Close the bound window. The portal session is still bound at this point
    (right after validation), so the window spans the whole bound lifetime."""
    _testvpn(state["serial"], "mark", label="bound_end")
    return {"marked": "bound_end"}


def step_pull_vpn_sink(state: dict) -> dict:
    serial = state["serial"]
    contents = adb_helper.shell(
        serial, f"run-as {APP_PACKAGE} cat {VPN_SINK_RELATIVE}", timeout=10, check=False
    )
    out = state["artifacts_dir"] / "vpn-sink.jsonl"
    out.write_text(contents)
    return {"path": str(out), "size": len(contents)}
```

- [ ] **Step 3: Insert the steps into `STEPS`** — change the `STEPS` list so the relevant region reads:

```python
    step("wait_for_captive", step_wait_for_captive),
    step("grant_vpn", step_grant_vpn),
    step("start_test_vpn", step_start_test_vpn),
    step("liveness_probe", step_liveness_probe),
    step("launch_debug_portal", step_launch_debug_portal),
    step("wait_portal_screen", step_wait_portal_screen),
    step("submit_login", step_submit_login),
    step("wait_validated", step_wait_validated),
    step("mark_bound_end", step_mark_bound_end),
    step("pull_vpn_sink", step_pull_vpn_sink),
    step("pull_logcat", step_pull_logcat),
```

- [ ] **Step 4: Tear down the VPN in `finally`** — in `main()`'s `finally` block, immediately after `if serial:` and before the logcat capture `try`, add:

```python
        if serial:
            try:
                adb_helper.shell(
                    serial,
                    f"am start -n {TESTVPN_ACTIVITY} --es gatepath.testvpn.action stop",
                    timeout=15,
                    check=False,
                )
            except Exception:  # noqa: BLE001 — teardown must never mask the rc
                pass
```

(Keep the existing logcat-capture block that follows.)

- [ ] **Step 5: Syntax check + commit**

Run: `python3 -c "import ast; ast.parse(open('tests/e2e-android/scenario/run-scenario.py').read())"`
Expected: no output (parses clean).

```bash
git add tests/e2e-android/scenario/run-scenario.py
git commit -m "test(android-e2e): drive the test VPN sink in the scenario (grant/start/probe/mark/pull)"
```

---

### Task 5: Release-safety guard

Prove the apparatus cannot ship: assert the merged **release** manifest declares no VpnService/`BIND_VPN_SERVICE`, while the **debug** manifest does (positive control, so the guard itself is meaningful).

**Files:**
- Create: `tests/e2e-android/guard/check_release_manifest.py`

**Interfaces:**
- Consumes: AGP merged-manifest intermediates produced by `processDebugManifest` / `processReleaseManifest`.
- Produces: a script `check_release_manifest.py <android-app-dir>` exiting non-zero if release contains the markers or debug lacks them.

- [ ] **Step 1: Write the guard** — create `tests/e2e-android/guard/check_release_manifest.py`:

```python
#!/usr/bin/env python3
"""Guard: the no-leak test VPN apparatus must never ship.

Asserts the merged RELEASE manifest contains none of the markers below, and the
merged DEBUG manifest contains all of them (positive control — proves the guard
is actually looking at real manifests, not vacuously passing).

Usage: check_release_manifest.py <android/app dir>
Run after: ./gradlew :app:processDebugManifest :app:processReleaseManifest
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKERS = ("GatepathTestVpnService", "BIND_VPN_SERVICE", "TestVpnControlActivity")


def merged_manifest(app_dir: Path, variant: str) -> Path:
    # AGP path varies by version; glob defensively for the variant's merged manifest.
    hits = sorted(app_dir.glob(f"build/intermediates/**/{variant}/**/AndroidManifest.xml"))
    hits = [h for h in hits if "merged" in str(h).lower()]
    if not hits:
        raise SystemExit(f"no merged manifest found for '{variant}' under {app_dir}/build")
    return hits[0]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_release_manifest.py <android/app dir>", file=sys.stderr)
        return 2
    app_dir = Path(argv[1])
    failures: list[str] = []

    release = merged_manifest(app_dir, "release").read_text()
    for m in MARKERS:
        if m in release:
            failures.append(f"RELEASE manifest leaks the test VPN marker: {m}")

    debug = merged_manifest(app_dir, "debug").read_text()
    for m in MARKERS:
        if m not in debug:
            failures.append(f"DEBUG manifest unexpectedly missing {m} — guard may be vacuous")

    if failures:
        for f in failures:
            print(f"  ✗ {f}", file=sys.stderr)
        return 1
    print("  ✓ release manifest clean; debug manifest carries the apparatus")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 2: Verify locally**

Run:
```bash
cd android && ./gradlew --no-daemon :app:processDebugManifest :app:processReleaseManifest && cd ..
python3 tests/e2e-android/guard/check_release_manifest.py android/app
```
Expected: `✓ release manifest clean; debug manifest carries the apparatus`, exit 0.

- [ ] **Step 3: Add a CI job** — in `.github/workflows/android-e2e.yml`, add a second job after `emulator-e2e`:

```yaml
  release-vpn-guard:
    name: Release build excludes the test VPN
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '21'
      - uses: android-actions/setup-android@v3
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Process manifests
        working-directory: android
        run: ./gradlew --no-daemon :app:processDebugManifest :app:processReleaseManifest
      - name: Assert release excludes the test VPN
        run: python3 tests/e2e-android/guard/check_release_manifest.py android/app
```

- [ ] **Step 4: Commit**

```bash
git add tests/e2e-android/guard/check_release_manifest.py .github/workflows/android-e2e.yml
git commit -m "test(android-e2e): guard that release builds exclude the test VPN apparatus"
```

---

### Task 6: Full E2E run + negative control (the eval must be able to fail)

Run the whole thing on an emulator, confirm green, then deliberately break confinement and confirm it goes RED. An eval that cannot fail is worthless.

**Files:**
- (No committed source changes; this is verification. The temporary leak edit is reverted.)

**Interfaces:**
- Consumes: everything from Tasks 1–5.

- [ ] **Step 1: Full green run** — boot an API-34 google_apis emulator with mockportal at `10.0.2.2:18080`, then:

```bash
bash tests/e2e-android/scenario/ci-script.sh
python3 tests/e2e-android/driver/assertions.py tests/e2e-android/artifacts
```
Expected: `all assertions passed`, including `✓ vpn.liveness` and `✓ vpn.confinement`. Inspect `tests/e2e-android/artifacts/vpn-sink.jsonl`: a `203.0.113.7` line before `bound_begin`, and **no** packet lines between `bound_begin` and `bound_end`.

- [ ] **Step 2: Negative control** — temporarily neuter the bind to force a leak. In `android/app/src/main/java/cc/grepon/gatepath/ui/GatepathWebView.kt:147`, comment out the bind:

```kotlin
        // connectivityManager.bindProcessToNetwork(network)  // NEGATIVE CONTROL — DO NOT COMMIT
```

Rebuild the debug APK and re-run Step 1.
Expected: assertions FAIL with `✗ vpn.confinement: LEAK: bound-phase Gatepath traffic to 10.0.2.2:18080 escaped onto the default (VPN) network`.

- [ ] **Step 3: Revert the negative control**

Run: `git checkout android/app/src/main/java/cc/grepon/gatepath/ui/GatepathWebView.kt`
Expected: file restored; `git status` clean for that file. Rebuild and re-run Step 1 → green again.

- [ ] **Step 4: Burn-in** — re-run Step 1 three more times (or trigger the `android-e2e` workflow via `workflow_dispatch` 3×). Expected: green all runs (confirms non-flaky before the hard gate). If flaky, tune the `time.sleep` settles in Task 4 / the `wait_*` windows; do not merge a flaky gate.

- [ ] **Step 5: Record the result** (no code commit — note the negative-control + burn-in outcome in the PR description for Task 7's PR).

---

### Task 7: Documentation + ROADMAP flip

Reflect the now-proven invariant. Keep the project's honest framing.

**Files:**
- Modify: `tests/e2e-android/HARNESS_NOTES.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/SECURITY_MODEL.md`

**Interfaces:**
- Consumes: the proven harness from Tasks 1–6.

- [ ] **Step 1: HARNESS_NOTES** — append a section to `tests/e2e-android/HARNESS_NOTES.md`:

```markdown
## No-leak sentinel (ROADMAP P0.1)

A debug-only `VpnService` (`android/app/src/debug/.../testvpn/`) becomes the
system default network and logs every packet the Gatepath app emits while
unbound to `files/vpn-sink.jsonl`. Because `bindProcessToNetwork(wifi)` bypasses
the VPN, the sink is a leak detector:

- `liveness_probe` sends an UNBOUND UDP burst to the sentinel `203.0.113.7` — it
  MUST appear in the sink (proves the sink intercepts the default route).
- The portal session runs bound to WiFi between the `bound_begin`/`bound_end`
  marker lines — the sink MUST be packet-silent there (proves confinement).

`appops set cc.grepon.gatepath ACTIVATE_VPN allow` suppresses the consent dialog
(no root). The apparatus is `src/debug/` only; `release-vpn-guard` CI asserts the
release build excludes it. Negative control: comment out the bind at
`GatepathWebView.kt` and `vpn.confinement` goes RED.
```

- [ ] **Step 2: ROADMAP** — in `docs/ROADMAP.md`, update the P0.1 status line. Replace:

```markdown
**Status:** **desktop confinement gate is now proven** — see P0.2. Android: not
started.
```

with:

```markdown
**Status:** **proven on both platforms.** Desktop — see P0.2. Android — a
debug-only `VpnService` leak detector in `tests/e2e-android` asserts an unbound
liveness probe reaches the default route while the WiFi-bound portal session
leaves it packet-silent (negative-control verified; release builds provably
exclude the apparatus).
```

And in the Android bullet under P0.1, replace `not started —` with `**done** —`.

- [ ] **Step 3: SECURITY_MODEL** — in `docs/SECURITY_MODEL.md`, after the Android binding claim (around line 43), add:

```markdown
> This is proven, not just asserted: the `tests/e2e-android` no-leak sentinel
> runs a debug-only VpnService as the default network and fails the build if any
> bound-session traffic escapes onto it (ROADMAP P0.1).
```

- [ ] **Step 4: Commit**

```bash
git add tests/e2e-android/HARNESS_NOTES.md docs/ROADMAP.md docs/SECURITY_MODEL.md
git commit -m "docs: Android no-leak sentinel proven (ROADMAP P0.1)"
```

- [ ] **Step 5: Open the PR**

```bash
git push -u origin feat/android-no-leak-sentinel
gh pr create --base main --title "feat(android): no-leak sentinel — prove WebView confinement at the network level (P0.1)" --body "Implements docs/superpowers/specs/2026-06-29-android-no-leak-sentinel-design.md. Negative control + burn-in results in comments."
```

---

## Self-Review

**1. Spec coverage:**
- Mechanism (debug VpnService leak detector) → Tasks 1–2. ✓
- Zero production-code changes → enforced (all in `src/debug/`; the only `src/main` touch is the *temporary, reverted* negative control in Task 6). ✓
- Two-sided proof D1/D2/D3 → Task 3 (D1/D2) + existing gateway assertion (D3, untouched). ✓
- Liveness anti-false-green gate → Task 3 `check_vpn_confinement` (returns before D2 if no sentinel). ✓
- Marker-delimited bound window (clock-free) → Tasks 2 + 4. ✓
- Missing/empty sink = hard fail → Task 3 Step 5 `main()`. ✓
- Release safety (src/debug + guard) → Task 5. ✓
- Spike-first decision gate → Task 1. ✓
- Negative control + burn-in → Task 6. ✓
- CI hard gate after burn-in → Task 6 Step 4 + the assertion already runs in `android-e2e.yml` "Run host-side assertions". ✓
- Docs flip → Task 7. ✓
- Sentinel `203.0.113.7`, portal `10.0.2.2`, UDP probe → consistent across tasks. ✓

**2. Placeholder scan:** No TBD/TODO; all code blocks complete; the only `<…>` are CLI argument placeholders in usage strings.

**3. Type consistency:** `ACTION_START/STOP/MARK`, `EXTRA_LABEL`, `SINK_FILE="vpn-sink.jsonl"`, `gatepath.testvpn.action`/`gatepath.testvpn.label`, marker labels `bound_begin`/`bound_end`, sentinel `203.0.113.7`, step names (`grant_vpn`, `start_test_vpn`, `liveness_probe`, `mark_bound_end`, `pull_vpn_sink`) are identical in the producing and consuming tasks (service ↔ activity ↔ run-scenario ↔ assertions ↔ EXPECTED_STEPS).

**Note on the bound-window/bind gap:** `bound_begin` is marked just before `launch_debug_portal`, a few hundred ms before the WebView actually calls `bindProcessToNetwork`. The app does no network I/O in that gap, so the window stays silent; flagging anything there is conservative (fails safe), per the spec.
