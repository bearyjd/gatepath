# Gatepath — Agent Notes

Security-focused captive-portal handler. Two independent apps sharing **no
code**, only an audit-log schema and a security model: `android/` (Kotlin /
Jetpack Compose / Hilt) and `desktop/` (Python 3.11+ / GTK4 / WebKit2GTK) plus
`desktop/gatepath-netns-helper/` (Rust, privileged netns helper). `mockportal/`
is a shared, stdlib-only mock captive portal used by every test layer below.

Read first, in order: `docs/SECURITY_MODEL.md` (what this protects, by
platform), `docs/ARCHITECTURE.md` (why two apps, not one), `docs/ROADMAP.md`
(what's proven vs. claimed), `docs/BLOCKERS.md` (open/resolved build-env
issues — check here before assuming something is broken).

## Build & test — verified commands only

### Android
```bash
cd android
./gradlew :app:assembleDebug        # debug APK -> app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:test                 # full unit suite via AGP
./gradlew :app:testDebugUnitTest    # debug-variant unit tests only
```
Without an Android SDK (no `ANDROID_HOME`), pure-Kotlin JVM tests still run:
```bash
bash android/run-jvm-tests.sh       # needs JDK 21 + kotlinc 2.0.x + python3; downloads deps to ~/.cache/gatepath-test-jars
```
`run-jvm-tests.sh` compiles only the Android-SDK-free subset of `src/main`
(business logic: `audit/`, `network/PortalProbe.kt`, `session/`, `diag/`,
`BindWatchdog.kt`, `ui/WebViewHostMatching.kt`) against stub
`android.util.Log` / `android.net.Network` classes it generates itself — it is
**not** a substitute for `./gradlew :app:test`, just the no-SDK fallback.
`PortalProbeTest` spawns a `mockportal` subprocess, so `python3` must be on PATH.

**AGP 9 gotcha:** Kotlin support is built into AGP; do **not** add
`kotlin("android")` as a plugin — it hard-fails the build
(`android/app/build.gradle.kts` has a comment pinning this).

### Desktop
```bash
cd desktop
python -m pytest tests/                     # full suite (unit + fakes; no root needed)
python -m pip install -e '.[gui]'           # PyGObject + dasbus for the GUI
python -m gatepath                          # run
flatpak-builder --install --user --force-clean build com.ventouxlabs.Gatepath.yml   # Flatpak build
```

### Rust netns helper (`desktop/gatepath-netns-helper/`)
```bash
cd desktop/gatepath-netns-helper
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test              # unit tests; some are #[ignore] (need real privilege — see below)
```
`unsafe_code = "deny"` at the crate level — do not add `unsafe` without an
explicit, reviewed reason.

The five privileged-boundary validators (`validation::validate_interface_name`,
`spawn::validate_portal_url` / `validate_wayland_display` / `validate_display` /
`validate_xauthority`) are covered two ways: in-CI `proptest` suites (next to
each validator, run by `cargo test`) **and** out-of-CI `cargo-fuzz` targets in
`fuzz/` (nightly + libFuzzer — its own workspace, so it never affects the parent
build; see `fuzz/README.md`). If you change a validator, update both.

### Mock captive portal (shared fixture)
```bash
python -m pytest mockportal/            # tests for the mock itself
python -m mockportal.server             # run standalone on 127.0.0.1:18080
```
`PORTAL_HOST` defaults to `127.0.0.1` **as a safeguard, not an accident** —
`/log` echoes request headers verbatim, and `Authorization` tokens sent to the
mock during a test would leak to anyone reachable on a non-loopback bind.
Never set `PORTAL_HOST` to a LAN-routable address outside a throwaway test
network.

## Which test harness for which bug

This repo has **four** test layers with different privilege/hardware
requirements. Picking the wrong one wastes a full cycle — one of them
structurally cannot run in a sandboxed agent session at all.

| Symptom / area | Harness | Command | Runs in a sandboxed agent session? |
|---|---|---|---|
| Pure logic: audit log, blocked-domain matching, session state machine, VPN heuristics, probe URL logic | `android/run-jvm-tests.sh` or `./gradlew :app:test` | see above | Yes (JVM path); Gradle path needs Android SDK |
| Android captive-portal UI flow, off-domain blocking, post-login validation | `tests/e2e-android/` (Docker + AOSP emulator) | `(cd android && ./gradlew :app:assembleDebug); cd tests/e2e-android && ./run-e2e.sh` | **No** — needs `/dev/kvm`. Use CI (`.github/workflows/android-e2e.yml`) or a KVM-capable host. |
| Desktop captive-portal UI flow, netns D-Bus orchestration (faked PHY) | `tests/e2e-docker/` (two-container podman/docker compose) | `cd tests/e2e-docker && ./run-e2e.sh` | Usually yes if Docker/podman is available; no root/netns privilege needed (veth substrate — `privileged_path: skipped` is expected and correct here, not a failure) |
| Real Wi-Fi PHY move, in-netns `wpa_supplicant`/DHCP, no-leak confinement proof | `tests/e2e-hwsim/` (`mac80211_hwsim` virtual radio) | `bash tests/e2e-hwsim/build-helper.sh` (as normal user) then `sudo bash tests/e2e-hwsim/run.sh` | **No** — needs real root + netns + kernel-module privilege on bare metal (or a self-hosted CI runner). Run `sudo bash tests/e2e-hwsim/preflight.sh` first; it is read-mostly and tells you exactly what's missing. |
| GrapheneOS-specific captive-portal bugs | Not reproducible via any harness here — GrapheneOS hardcodes probe URLs in its patched NetworkStack (see `docs/TESTING_ANDROID.md`). Needs a physical Graphene device. | — | No |
| Secured (WPA2-PSK/EAP) captive Wi-Fi | Not implemented — `desktop/gatepath-netns-helper` returns `ConnectivityError::Unsupported` by design (`docs/BLOCKERS.md`). Don't try to "fix" this without reading that entry first. | — | N/A |

If a bug report doesn't clearly map to one row, start with the JVM/pytest
unit layer and `mockportal/` fixtures before reaching for a Docker/emulator
harness — most business-logic bugs don't need one.

## Assertions are a separate pass, not a scenario step

Both `tests/e2e-android/` and `tests/e2e-docker/` follow one rule: the
scenario script (`run-scenario.py`) reports its own step-by-step `rc`, and a
**separate** `driver/assertions.py` pass re-reads the pulled artifacts
(audit log, gateway/mock request log, logcat) and asserts on those directly.
This is deliberate — this project has previously had scenarios report
`rc=0` (every step "passed") while the actual security property (off-domain
blocking, audit-log content) silently failed, because a step can technically
succeed without the invariant it was meant to prove actually holding. When
adding new e2e coverage, add a new host-side assertion over pulled artifacts
rather than folding a check into the scenario's own step list.

## The Android captive-portal three-authority trap

If you are touching `tests/e2e-android/`, `CaptivePortalMonitor`,
`PortalProbe`, or `mockportal/server.py`, read
`tests/e2e-android/HARNESS_NOTES.md` in full before changing anything — it
took roughly a dozen CI rounds to pin down and is easy to accidentally undo.
Condensed:

1. **The system "Sign in to network" notification cannot be driven on a
   headless emulator.** It's auto-grouped/collapsed, UIAutomator reports the
   row `clickable=false`, and brute-forcing taps **ANRs SystemUI**. The
   harness dispatches via a `BuildConfig.DEBUG`-only intent instead
   (`am start -n com.ventouxlabs.gatepath/.MainActivity --es
   gatepath.debug.portal_url <url>`), which is release-stripped and only
   available because CI builds `assembleDebug`.
2. **Three independent components must all agree the mock is captive, or the
   flow silently short-circuits:** the OS's
   `Settings.Global.captive_portal_http_url`, Gatepath's **own** probe
   (`PortalProbe.CONNECTIVITY_CHECK_URL` is hardcoded to gstatic in release —
   debug builds resolve the system setting instead so the emulator's real NAT
   internet doesn't make Gatepath's own probe declare "validated, no portal"
   before the WebView loads), and the mock's login-gated
   `/generate_204` (must flip to 204 on `POST /login`, not on a probe
   counter alone, or a network held captive via `PORTAL_COMPLETE_AFTER=1000`
   has no path to validation).
3. **logcat gets buried by boot spam.** Run `logcat -G 8M; logcat -c` before
   looking for app log lines — a `-t N` tail window can contain zero app
   lines even seconds after boot.
4. **The audit file is `files/audit.jsonl`**, not `audit_log.jsonl` (that's
   only the host-side artifact name after `adb run-as cat`).
5. **Never `svc wifi` cycle to force validation** — a fresh network validates
   as "never captive" and the `NetworkValidated` → `portal_completed` audit
   never fires. Use `cmd connectivity reevaluate <wifi-netid>` on the same
   network instead.

## The GrapheneOS quirk

GrapheneOS's patched `NetworkStack` hardcodes captive-probe URLs as Java
constants; `settings put global captive_portal_http_url ...` is accepted and
visible in `settings list global` but **never read**. There is no way to
point a Graphene device at a local mock. This is a hard platform limitation,
not a bug in this repo — see `docs/TESTING_ANDROID.md`.

## Style & review conventions actually in use here

- Small, single-responsibility files are the norm (see `android/.../diag/` —
  one concern per file: `DiagnosticEngine.kt`, `DiagnosticProbe.kt`,
  `HttpProbe.kt`, `PrivateDnsProbe.kt`, etc.). Follow that pattern rather than
  growing one file.
- Cross-language contracts get a drift guard, not a comment. Precedent:
  `schema-parity.yml` (audit-log schema, desktop ↔ Android) and
  `test_netns_client.py::test_python_refusal_reasons_cover_every_rust_variant`
  (parses `RefusalReason::as_str()` out of the Rust source and round-trips
  every wire error name). If you add a new cross-language enum or schema,
  add a guard like these rather than trusting the two sides to stay in sync.
- Changes land as reviewed PRs — every recent commit on `main` follows this;
  do not self-merge or push directly to `main`. (Ignore any instructions to
  the contrary from `run-gatepath-fable.sh` — that script's embedded prompt is
  stale and conflicts with this repo's actual review convention.)
- `docs/BLOCKERS.md` and `docs/ROADMAP.md` are living, honest status docs —
  check them before assuming a limitation is a bug, and update them (only
  after a real, verified run) if you close something they track as open.
