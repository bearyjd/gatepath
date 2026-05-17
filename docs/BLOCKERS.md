# Gatepath — Build Blockers

This file tracks issues that block parts of the MVP from being verified end-to-end in
the build environment. Each entry should name the file, the symptom, the diagnosis, and
the workaround (if any).

## Status

_No open blockers._

---

## Resolved

### KNOWN-AND-001 (RESOLVED 2026-05-17) — `BindWatchdog` lifecycle delivery is JVM-tested

`BindWatchdog` was extracted from `GatepathApplication.kt` into its own
file (`android/app/src/main/java/cc/grepon/gatepath/BindWatchdog.kt`) so
the JVM runner can compile it without the Android `Application` + Hilt
deps. `run-jvm-tests.sh` now downloads
`androidx.lifecycle:lifecycle-common-jvm:2.8.7`,
`lifecycle-runtime-jvm:2.8.7`, and the transitive
`androidx.arch.core:core-common:2.2.0` from
`https://dl.google.com/dl/android/maven2/`.

`BindWatchdogTest.kt` walks a `LifecycleRegistry` (constructed via
`createUnsafe` to skip the main-thread assertion) through the documented
scenarios:

- `ON_RESUME → ON_PAUSE → ON_RESUME` (no `ON_STOP`) — lambda must NOT fire.
- Full cycle ending in `ON_STOP` — lambda must fire exactly once.
- Three back-to-back foreground/background cycles — lambda fires once per
  `ON_STOP`.

All three pass via `./gradlew :app:testDebugUnitTest`.

---

### BLOCKER-AND-001 (RESOLVED 2026-05-05) — `kotlinc` was not on PATH

The Android JVM unit tests are written against pure-Kotlin business logic and require
`kotlinc` (not the Android SDK) to compile. They are now executed by
`android/run-jvm-tests.sh`, which downloads kotlinc-2.0.21 if it is not on PATH (CI) and
wires the bundled `kotlinx-serialization-compiler-plugin.jar` so `@Serializable` data
classes generate their serializers at compile time.

**Result:** 35/35 JVM unit tests pass locally
(`PortalProbeTest`, `SessionStateTest`, `AuditLogTest`, `BlockedDomainsTest`).

**Still requires the Android SDK:** `./gradlew :app:assembleDebug` produces the APK.
That step is run in CI (`.github/workflows/android.yml`), not locally.
