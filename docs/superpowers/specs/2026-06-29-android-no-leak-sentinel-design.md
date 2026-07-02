# Android No-Leak Sentinel — Design Spec

**Status:** Approved design, pending implementation plan
**Date:** 2026-06-29
**Roadmap item:** P0.1 — No-leak sentinel test (Android side)
**Branch:** `feat/android-no-leak-sentinel`

## Problem

Gatepath's core promise (`docs/SECURITY_MODEL.md:41-43`): every probe and WebView
request is pinned to the captive-portal `Network` via
`ConnectivityManager.bindProcessToNetwork()`, so captive traffic **cannot leak**
onto the user's other connections (VPN, default route, normal browsing).

Today nothing *proves* that on Android. The existing emulator E2E
(`tests/e2e-android`) asserts only that **off-domain hostnames** don't appear in
the mock gateway's request log — an **application-level** check (WebView
same-origin navigation blocking), not a **network-level** proof of confinement.
ROADMAP P0.1 calls this the highest-leverage gap: "stand up a sentinel the
confined client *must not* reach, and fail the test if it does."

The desktop side already has this (`tests/e2e-docker` + `tests/e2e-hwsim`): a
`trusted-sentinel` on a separate network that the in-netns probe must not reach,
with a `LEAK:` hard failure if it does. This spec brings the equivalent to
Android.

## The emulator obstacle (why the naive approach fails)

On the CI emulator (`reactivecircus/android-emulator-runner`, google_apis API 34)
**everything NATs to a single host**. The bound WebView already loads the portal
at `http://10.0.2.2:18080/portal` *while bound to the captive WiFi `Network`*
(confirmed by the existing `/portal`-GET assertion). So a plain "second server on
the host" is **not** off-captive — the bound process can already reach it. A
network-level sentinel needs an address reachable **unbound but not when bound to
WiFi**, which a single-NAT emulator does not naturally provide.

## Chosen mechanism: a local, debug-only `VpnService` leak detector

A `VpnService`, when active, becomes the **system default network**: all traffic
*not* bound to a specific `Network` flows through its TUN interface. A process
that calls `bindProcessToNetwork(wifi)` **bypasses** the VPN. That makes the VPN's
TUN a perfect **leak detector**:

- If confined WebView traffic ever escapes to the default route, the VPN sink
  records it → **LEAK**.
- If confinement holds, the sink stays silent during the bound window, while the
  portal still loads over WiFi (proven independently by the mock gateway log).

This is faithful to the literal `SECURITY_MODEL.md` "VPN active" scenario, yet
fully deterministic and headless — the VPN is a **local sink** (a black hole that
logs and never forwards), not a real tunnel or second uplink.

### Why an in-process VpnService is sound

The TUN file descriptor is opened by `establish()` *before* any process bind, and
reading a TUN fd is **not** subject to `bindProcessToNetwork`. So the sink keeps
capturing the unbound default route even while the WebView process is bound to
WiFi. The service is restricted to the Gatepath package
(`addAllowedApplication(self)`) to keep the sink low-noise.

## Architecture

```
        ┌─────────────────────────── Android emulator (API 34) ───────────────────────────┐
        │   captive WiFi Network  ──(bindProcessToNetwork)──▶  Gatepath WebView            │
        │        │  reaches mock portal 10.0.2.2:18080  ✓ (legit, must succeed)            │
        │   GatepathTestVpnService  ◀── DEFAULT route for everything NOT bound             │
        │        │  logs every dst → files/vpn-sink.jsonl (black hole, no forward)         │
        │        ├─ LIVENESS CONTROL: an *unbound* probe to the sentinel ──▶ MUST be logged │
        │        └─ CONFINEMENT:      the *bound* WebView portal load    ──▶ MUST be silent │
        └──────────────────────────────────────────────────────────────────────────────────┘
```

**Two independent observers, same run:**
- Mock gateway `/log` proves the portal *was* reached — over WiFi (existing).
- VPN sink proves that same traffic did *not* go over the default route (new).

### Components

1. **`GatepathTestVpnService`** (`android/app/src/debug/`) — a `VpnService` that
   captures the default route for the Gatepath package only and appends
   `{dst, port, proto, t}` per observed packet to `files/vpn-sink.jsonl`. A dumb
   logger: no phase logic, no forwarding. **Debug source set only** — physically
   absent from release builds.

2. **`TestVpnControl`** (`android/app/src/debug/`) — a debug-only Activity/Receiver
   handling harness actions: `start-vpn`, `stop-vpn`, and `liveness-probe` (an
   **unbound** short burst of UDP datagrams to the sentinel marker from the
   Gatepath process). UDP is deliberate: one datagram is one logged packet with no
   TCP SYN-retransmit tail that could bleed into the bound window. Debug source set
   only.

3. **Harness steps** (`tests/e2e-android/scenario/run-scenario.py`) — woven into
   the existing 16-step scenario; also records the phase-window timestamps.

4. **Assertions** (`tests/e2e-android/driver/assertions.py`) —
   `check_vpn_confinement()`, which buckets sink entries by the harness-recorded
   timestamps and applies the proof below.

**Production source set gets ZERO changes.** The bound-phase window is bracketed
by harness timestamps, not by hooks in production code, so the code under test
stays byte-identical to what ships.

## Scenario flow

New/changed steps in **bold**, woven into the existing scenario:

```
 1. connect                                          (existing)
 2. reset_settings
 3. install                                          (debug APK now also carries the test VPN)
 4. reset_gateway
 5. set_probe_urls
 6. cycle_wifi
 7. wait_for_captive                                  WiFi settles as the captive Network FIRST
 8. ▶ grant_vpn        adb shell appops set <pkg> ACTIVATE_VPN allow   (no consent dialog; emulator rooted)
 9. ▶ start_test_vpn   debug action → GatepathTestVpnService becomes DEFAULT, allowed-app = Gatepath only
10. ▶ liveness_probe   debug action → app sends an UNBOUND UDP burst to the sentinel (203.0.113.7:9);
                       record t_unbound_probe                          → egresses default route → sink
11. ▶ settle: confirm the socket is closed and the TUN is quiescent (~2s no sink activity),
       THEN record t_bound_begin
    launch_debug_portal   (existing) — app calls bindProcessToNetwork(wifi), loads 10.0.2.2/portal
12. wait_portal_screen    (existing) — /portal GET (Android UA) arrives at the mock over WiFi  ✓
13. submit_login / wait_validated   (existing)
    ▶ (record t_bound_end)
14. ▶ pull_vpn_sink    run-as cat files/vpn-sink.jsonl     (same pattern as pull_audit_log)
15. pull_logcat / pull_audit_log / fetch_gateway_log   (existing)
16. ▶ stop_test_vpn    debug action stopService (in a finally — never leave the VPN up)
17. cleanup_settings / disconnect
```

**Sentinel marker:** `203.0.113.7` (TEST-NET-3, RFC 5737 — guaranteed never a real
host). It exists only as the liveness control; the real confinement *subject* is
the portal host `10.0.2.2`, which must never appear in a bound-window sink line.
The liveness probe need not reach anything — recording the outbound datagram's
destination is the signal. UDP (not TCP) avoids a SYN-retransmit tail leaking into
the bound window, and no responder is required.

## The proof (assertions)

> **Implementation note:** the shipped harness delimits the bound window with
> `bound_begin`/`bound_end` **marker lines** appended into the sink (append order),
> not wall-clock timestamps — this avoids any host↔device clock comparison. The
> timestamp-bucketing description below was the original design; the marker-based
> approach in the implementation plan supersedes it.

`check_vpn_confinement()` over `vpn-sink.jsonl`. **Order matters** — the liveness
gate is what makes a silent sink *mean* something.

**D1 — Liveness gate (anti-false-green). MUST pass first.**
The sink must contain ≥1 entry with `dst == 203.0.113.7` whose `t` falls in
`[t_unbound_probe, t_bound_begin)`.
Fail → `"liveness control failed: the VPN sink never captured the unbound probe —
the sink isn't actually intercepting the default route, so a silent bound phase
proves nothing."`
This is the load-bearing guard: without it, a broken/never-default VPN would make
every run look confined. (Mirrors the desktop side's refusal to let "nothing
observed" silently pass.)

**D2 — Confinement (the invariant).**
Zero sink entries with `t` in `[t_bound_begin, t_bound_end]` — for *any*
destination. During a bound portal session the app does no other network I/O
(`SECURITY_MODEL.md:70-87`), so the TUN must be completely silent.
Fail → `"LEAK: bound-phase Gatepath traffic to <dst>:<port> escaped onto the
default (VPN) network"`.

**D3 — Positive corroboration (reuses existing).**
The mock gateway `/log` still shows the `/portal` GET from an Android UA → the
portal genuinely loaded **over WiFi**. D3 + D2 = *loaded, and did not leak*.

### Result matrix

| D1 liveness | D2 bound window | Verdict |
|---|---|---|
| present | silent | ✅ confinement proven |
| **absent** | (any) | ❌ vacuous — sink not intercepting; fix harness |
| present | **has entry** | ❌ LEAK — names the escaping dst |

### Failure-mode hardening (no silent skips)

- `vpn-sink.jsonl` missing or unparseable → hard FAIL, never skip. A harness bug
  must not masquerade as confinement.
- Phase-tag integrity: the unbound liveness probe (step 10) is a UDP burst (no
  retransmit tail); the harness closes the probe socket and waits for TUN
  quiescence (~2s of no sink activity) **before** recording `t_bound_begin`, so a
  late control packet cannot be mis-bucketed into the bound window. Phase
  classification is by harness-recorded timestamps in `assertions.py`. Erring
  toward flagging a bound-window packet is conservative (fails safe).

## Release safety (security-critical)

A full-capture VpnService must never ship:

- Entire apparatus lives in **`android/app/src/debug/`** → service class and its
  manifest `<service>` + `BIND_VPN_SERVICE` declaration are **physically absent**
  from release builds (manifest merger drops the debug manifest).
- **Production source set untouched** — nothing references the debug classes.
- **Guard check** (new, cheap, styled like the existing `grep — no
  requests/httpx/aiohttp` job): assert the *merged release manifest* and release
  APK declare no `VpnService` and no `BIND_VPN_SERVICE`. Proves the scaffolding
  cannot leak into production.

## CI integration

- New assertion runs inside the existing green `emulator-e2e` job
  (`.github/workflows/android-e2e.yml`) — extends, does not fork.
- Lands as a **hard (merge-blocking) gate** after a short burn-in (run the job a
  handful of times to confirm non-flaky). Soft-gating a no-leak invariant defeats
  its purpose.
- Add the release-manifest guard as a separate cheap job/step.

## De-risking

1. **Spike first.** Implementation task #1 is a thin spike:
   `appops set ACTIVATE_VPN allow` → start the VPN → capture one unbound packet
   headless. If the emulator fights VPN-as-default, we learn it in ~30 min, not
   after building everything. (Same fail-fast posture the desktop side used.)
2. **Negative control — the eval must be able to fail.** Verify that with the bind
   deliberately disabled, **D2 goes RED** and names the leak. An eval that can't
   fail is worthless; this is a required verification step, not optional.
3. **Fallback.** If the spike shows the emulator cannot do VPN-as-default headless,
   fall back to an instrumentation (`androidTest`) test that proves the binding via
   the per-`Network` API directly (no VPN-as-default needed). This would be the
   first instrumentation suite in the project.

## Open risks

- `appops`-based VPN-consent suppression + VPN-as-default headless is well-trodden
  but unverified **on this exact image** — hence spike-first.
- VPN-as-default timing/flakiness — addressed by burn-in before the hard gate.

## Out of scope

- Secured (WPA2-PSK/EAP) networks — intentional limitation, tracked elsewhere.
- The desktop sentinel (already done — `tests/e2e-hwsim`).
- Any change to production confinement code — this is an eval of the shipping path.

## File inventory

| File | New/changed |
|---|---|
| `android/app/src/debug/java/com/ventouxlabs/gatepath/testvpn/GatepathTestVpnService.kt` | new (debug-only) |
| `android/app/src/debug/java/com/ventouxlabs/gatepath/testvpn/TestVpnControl.kt` | new (debug-only) |
| `android/app/src/debug/AndroidManifest.xml` | new/extended (debug-only) |
| `tests/e2e-android/scenario/run-scenario.py` | +5 steps + phase timestamps |
| `tests/e2e-android/driver/assertions.py` | + `check_vpn_confinement()` |
| `.github/workflows/android-e2e.yml` | wire new assertion + release-manifest guard |
| `tests/e2e-android/HARNESS_NOTES.md`, `docs/ROADMAP.md`, `docs/SECURITY_MODEL.md` | doc the proven invariant |

## Success criteria

- The emulator E2E proves, at the **network level**, that bound WebView traffic
  does not escape onto the default route, with a liveness control that prevents a
  vacuous pass.
- A deliberately-unbound run goes RED (negative control verified).
- Release builds provably contain no VPN apparatus.
- ROADMAP P0.1 (Android) flips from "not started" to proven; `SECURITY_MODEL.md`
  framing can cite an eval, not just structure.
