# Android e2e harness — design notes & gotchas

Why this harness is built the way it is, condensed from the debugging that got
it green. Read this before changing `run-scenario.py`, the mock, or Gatepath's
captive-detection code — several of these are non-obvious and easy to undo.

## Symptoms that point back here

- The scenario reports `rc=0` (all steps ✓) but `driver/assertions.py` fails on
  `audit.portal_completed` or `gateway.portal_hit`.
- `wait_portal_screen` times out: "CaptivePortalActivity did not start" /
  "portal WebView never fetched /portal".
- `wait_validated` times out: "WIFI network never reached IS_VALIDATED".
- `pull_audit_log` returns `size=0` ("audit_log.jsonl missing or empty").
- logcat shows `GatepathMonitor: ... observed validated, no portal`.

## 1. Dispatch is via the debug intent, NOT the system notification

The original goal was to drive the real system `CAPTIVE_PORTAL` notification →
`CaptivePortalActivity`. **That is not achievable on a headless emulator:**

- The "Sign in to network" notifications are auto-grouped + collapsed
  (`groupKey ...g:ranker_group`); SystemUI rows report `clickable=false` to
  UIAutomator, and `input tap` on the row does not register as a click
  (`dumpsys notification` → `posttimeToFirstClickMs=-1`).
- Brute-forcing taps (multi-tap, swipe-to-expand) **ANRs SystemUI**.

So the harness uses the `BuildConfig.DEBUG`-gated debug intent (PR #34):
`am start -n cc.grepon.gatepath/.MainActivity --es gatepath.debug.portal_url <url>`,
which opens the same `PortalScreen` WebView. CI builds `assembleDebug`, so it's
present. Trade-off: the real system-intent → `CaptivePortalActivity` plumbing is
**not** exercised (it's untestable here); everything downstream of "PortalScreen
is showing the portal" is.

## 2. Three independent components must agree the mock is the captive authority

This is the trap that cost the most rounds: fixing one surfaces the next. All
three must point at / honour the mock, or the flow silently short-circuits.

| Authority | What it controls | How the harness makes it agree |
|---|---|---|
| OS `Settings.Global.captive_portal_http_url` | whether the OS marks the network captive / validated | `set_probe_urls` sets it to the mock `/generate_204` |
| Gatepath's **own** probe (`CaptivePortalMonitor` → `PortalProbe`) | whether Gatepath thinks it's captive | **debug-gated override** — see below |
| The **mock's** notion of "signed in" | whether `/generate_204` ever returns 204 | `POST /login` flips it; not counter-only |

**Gatepath's own probe is hardcoded to gstatic.**
`PortalProbe.CONNECTIVITY_CHECK_URL = http://connectivitycheck.gstatic.com/generate_204`.
The emulator has real internet via NAT, so Gatepath's own probe gets 204 from
gstatic and decides "validated, no portal" within ~2s — overriding the OS's
captive view and short-circuiting before the WebView loads. Fix
(`AppModule.provideCaptivePortalMonitor`, debug only): resolve
`captive_portal_http_url` and thread it into `CaptivePortalMonitor`. Release
builds keep gstatic and never read the setting.

**The mock must validate on login, not on a probe counter.**
`PORTAL_COMPLETE_AFTER` is set to `1000` (entrypoint.sh) so the network stays
reliably captive during detection and never auto-validates mid-detection. With a
counter-only mock that means `/generate_204` redirects forever — *no path to
validation*. So `POST /login` sets `authenticated`, after which `/generate_204`
returns 204. Callers that never log in (the desktop e2e, the unit tests) keep
the old counter behaviour, so this stays backward compatible.

## 3. Emulator / harness gotchas

- **logcat boot spam buries app logs.** After boot the emulator emits hundreds
  of `AiAiEcho ... package is updated` lines/sec — enough that even a `-t 3000`
  tail contains zero app lines, and the ring buffer rotates them out. Before
  reading app logs, `logcat -G 8M; logcat -c`, then dump the full `-d` and grep
  in Python. Don't rely on `-t N` windows or `-s TAG` under spam.
- **The audit file is `files/audit.jsonl`** (`AuditLog.init()` →
  `File(filesDir, "audit.jsonl")`), NOT `audit_log.jsonl` (that's the host-side
  artifact name). Pull via `run-as cat files/audit.jsonl`; it's appended from a
  coroutine on `NetworkValidated`, so poll a few seconds for non-empty content.
- **Do NOT `svc wifi` cycle to force validation.** A fresh network validates as
  "never captive", so `CaptivePortalMonitor` won't emit `NetworkValidated` and
  the `portal_completed` audit never fires. The same-network captive→validated
  transition is load-bearing for the audit. Nudge re-validation with
  `cmd connectivity reevaluate <wifi-netid>` instead (preserves the network).
- **Verify by the real signal.** `wait_portal_screen` waits for the WebView's
  actual `/portal` GET (Android UA) in the mock's `/log` — this both proves the
  load and stops the fast (~5s) validation from tearing the session down before
  the WebView loads.

## The validated end-to-end ordering

```
set_probe_urls          # OS authority → mock
launch_debug_portal     # am --es gatepath.debug.portal_url ... (clear logcat first)
wait_portal_screen      # wait for /portal GET (Android UA) in mock /log
submit_login            # POST /login → mock authenticates
wait_validated          # reevaluate same wifi netid; poll IS_VALIDATED
                        #   Gatepath monitor (debug-overridden to the mock) sees the
                        #   SAME network go captive→validated → NetworkValidated →
                        #   portal_completed audit
pull_audit_log          # run-as cat files/audit.jsonl
```

## No-leak sentinel (ROADMAP P0.1)

A debug-only `VpnService` (`android/app/src/debug/.../testvpn/`) becomes the system
default network and logs every packet the Gatepath app emits while unbound to
`files/vpn-sink.jsonl`. Because `bindProcessToNetwork(wifi)` bypasses the VPN, the
sink is a leak detector:

- `liveness_probe` sends an UNBOUND UDP burst to the sentinel `203.0.113.7` — it
  MUST appear in the sink (D1: proves the sink intercepts the default route).
- The portal session runs bound to WiFi between the `bound_begin`/`bound_end`
  marker lines — the sink MUST be packet-silent there (D2: proves confinement).

`appops set cc.grepon.gatepath ACTIVATE_VPN allow` suppresses the consent dialog
(no root). The apparatus is `src/debug/` only; `release-vpn-guard` CI asserts the
release build excludes it. Negative control: comment out the bind at
`GatepathWebView.kt` and `vpn.confinement` goes RED.

**Status:** implemented and wired into CI; pending its first green emulator run for
validation (the local dev-container emulator segfaults on boot, so CI is the gate).
The negative control has not been executed yet.
