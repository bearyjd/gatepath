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
`am start -n com.ventouxlabs.gatepath/.MainActivity --es gatepath.debug.portal_url <url>`,
which opens the same `PortalScreen` WebView. CI builds `assembleDebug`, so it's
present. Trade-off: the real system-intent → `CaptivePortalActivity` plumbing is
**not** exercised (it's untestable here); everything downstream of "PortalScreen
is showing the portal" is.

As of the confinement-state harness (2026-09), `step_launch_app` — a plain
`am start` with **no** debug extras — is the entry point in both
`STEPS_COVERING` and `STEPS_EXCLUDING`: the point of those modes is to prove
`CaptivePortalMonitor` classifies and (in `excluding` mode) opens the session
**on its own**, so forcing the session via `gatepath.debug.portal_url` would
defeat the test. `step_launch_debug_portal` (the intent above) still exists
in `run-scenario.py` but is no longer wired into either step list — it is
kept for manual use only (the workflow documented earlier in this section).

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

- **The mock must advertise `10.0.2.2`, not its own `0.0.0.0` bind address.**
  `mockportal-host` binds `0.0.0.0:18080` so the emulator's connection is
  accepted, but the confinement-state monitor path (unlike the old
  debug-intent path, which handed Gatepath the portal URL directly) follows
  the mock's own `Location` header from `/generate_204`. An advertised
  `0.0.0.0` sends the WebView to `http://0.0.0.0:18080/portal`, which the
  emulator cannot connect to (`net::ERR_CONNECTION_REFUSED`). Fixed via
  `build_server(..., advertised_host="10.0.2.2")` (`PORTAL_ADVERTISED_HOST` in
  `compose.yml` / `entrypoint.sh`), kept independent of the bind host in
  `mockportal/server.py` so `PORTAL_HOST`'s loopback safeguard is untouched.
- **`adb_helper.adb()` decodes with `errors="replace"`.** `logcat -d` output is
  not guaranteed valid UTF-8; one bad byte previously killed
  `step_start_test_vpn` with `UnicodeDecodeError` rather than surfacing a real
  step failure.
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

- **`am start` silently drops extras once the activity is already running.**
  It defaults to `FLAG_ACTIVITY_NEW_TASK`, so with `MainActivity` at standard
  launchMode Android just resumes the existing task: `onNewIntent` never fires,
  the extras are discarded, and the ONLY symptom is that nothing happens — no
  error, no log line. `launch_debug_portal` gets away with the plain form
  because the activity is not up yet when it runs. Any step firing a debug
  intent *later* in the scenario needs `am start --activity-single-top`.
  Do **not** reach for `am force-stop` instead: it delivers the intent, but it
  restarts the process and wipes retained ViewModel state, which can make a
  downstream assertion pass for the wrong reason (`bundle.capture_cleared`
  would have gone green because the process restarted, not because the
  validated transition cleared the capture).
- **Never `logcat -c` in a step that runs before `pull_logcat`.** It wipes the
  WebView evidence `off_domain` and `vpn.confinement` grep for, and both go red
  while the clearing step itself passes — so the failure points away from its
  cause. This is the #134/#135 clobbering trap from the other direction.
- **Signal step completion with a file, not a log line.** Given the spam and
  rotation above, a step that waits on logcat is waiting on a channel that may
  drop its answer. `pull_audit_log` and `_pull_sink` both poll app-private files
  via `run-as`; `write_bundle` does the same with `files/debug-bundle-uri.txt`.
  Delete the file first, so a leftover from an earlier attempt cannot read as
  the current run's success.

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
write_bundle            # am start --activity-single-top --ez gatepath.debug.write_bundle
                        #   poll for files/debug-bundle-uri.txt (written only AFTER
                        #   getUriForFile, so it proves the FileProvider authority
                        #   resolved); runs post-validation, which is also what makes
                        #   it the check that the capture did not outlive its incident
pull_bundle             # run-as cat cache/diagnostics/gatepath-diagnostics.txt
```

Note `write_bundle`/`pull_bundle` sit BEFORE `pull_logcat` in `STEPS` so the
app's own lines land in the captured buffer.

## No-leak sentinel (ROADMAP P0.1)

A debug-only `VpnService`, now shipped as a **separate, standalone debug app**
(`android/testvpn/`, package `com.ventouxlabs.gatepath.testvpn`) rather than a
build variant of Gatepath itself, becomes the system default network and logs
every packet it observes while Gatepath is unbound to
`files/vpn-sink.jsonl` (now under the `:testvpn` package's app-private
storage, pulled from there). The split into its own package is deliberate:
the original proof ran with Gatepath *owning* the VPN, which grants it socket
"protect" rights on its own tunnel — a shape that does not match a real user
running Tailscale or TorGuard, where Gatepath is an ordinary covered app. A
separate VPN owner closes that gap. The harness runs two VPN modes:

- **`covering`** — the `:testvpn` VPN covers Gatepath as a real third-party
  secure VPN would. `bindProcessToNetwork(wifi)` fails with `EPERM`,
  `MainViewModel` classifies `Tunnelled`, and no portal session opens at
  all. This mode reruns the no-leak proof under the realistic covered-app
  shape:
  - `liveness_probe` fires the **TCP** sentinel probe (a plain `Socket()`
    connect to `10.0.2.2:18081`, dispatched via the debug intent
    `gatepath.debug.sentinel_probe` on Gatepath's own `MainActivity` —
    **not** the old UDP burst, and not the `:testvpn` app's own `probe`
    action, since a `VpnService`'s own outbound traffic bypasses the tunnel
    it creates) — it MUST appear in the sink (D1: proves the sink
    intercepts the default route for a covered, non-owner app).
  - `settle_covering` gives the Tunnelled app 20s to misbehave, then lays
    `bound_end` itself — there is no `bound_begin`/portal session in this
    mode, so the sink MUST stay silent for that whole settle window
    (`check_vpn_silent_while_tunnelled` in `driver/assertions.py`: fail-closed
    confinement, deliberately with no positive-control WebView attempt to
    check against, since there's no WebView in this mode).
- **`excluding`** — Gatepath is excluded from the `:testvpn` VPN's
  disallowed-app list, the shipped product contract.
  `bindProcessToNetwork(wifi)` succeeds, `MainViewModel` classifies
  `Confined`, and `CaptivePortalMonitor` opens the session on its own (no
  debug intent) end-to-end through sign-in. This mode never runs
  `liveness_probe` or `settle_covering` and lays **no** VPN-sink markers at
  all — Gatepath's own traffic never rides the tunnel the sink watches, so
  the sink is not an oracle for it; `driver/assertions.py` asserts nothing
  about its contents in this mode and only notes it was pulled for the
  record.

`appops set com.ventouxlabs.gatepath.testvpn ACTIVATE_VPN allow` suppresses the
consent dialog (no root). The apparatus is `android/testvpn/`, a wholly
separate debug-only application module, never bundled with Gatepath's own
`app` module and never built for release; `release-vpn-guard` CI
(`tests/e2e-android/guard/check_release_manifest.py`) asserts Gatepath's own
merged RELEASE manifest contains none of `GatepathTestVpnService`,
`BIND_VPN_SERVICE`, or `TestVpnControlActivity`, with a positive control
confirming those same markers ARE present in `:testvpn`'s own DEBUG
manifest (so the check isn't vacuously passing). Negative control: comment
out the bind at `GatepathWebView.kt` and `vpn.confinement` goes RED in
`excluding`-adjacent manual testing (the `covering` mode's D2 has no
WebView to un-bind).

**Status:** proven — green and reproducible on the CI emulator
(`android-e2e`, now a `{covering, excluding}` matrix), covering mode's D1
liveness + D2 confinement pass non-vacuously, and excluding mode proves the
product contract end-to-end (audit entry with `confinement: confined`). The
VPN-as-default mechanism is also confirmed on a physical Pixel. Subtleties
the emulator surfaced, baked into the harness:
- Write phase markers from the harness via `run-as` append, NOT by `am start`-ing
  the control activity — Android drops the activity launch under rapid succession
  (the NoDisplay activity races its own `finish()`), so marks went missing.
- Use a routable sentinel host with a dedicated port (`10.0.2.2:18081`) the captive
  monitor never touches — an unroutable TEST-NET address never reached the TUN, and
  the captive monitor's own `:18080` probes are otherwise indistinguishable from
  WebView traffic in the sink.
- Settle until the unbound probe's TCP SYN retransmits drain before opening the
  bound window, else they bleed past `bound_begin` and read as a leak.
