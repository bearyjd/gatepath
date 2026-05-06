# Gatepath — Build Blockers

This file tracks issues that block parts of the MVP from being verified end-to-end in
the build environment. Each entry should name the file, the symptom, the diagnosis, and
the workaround (if any).

## Status

### KNOWN-AND-001 — `BindWatchdog` lifecycle delivery is JVM-untested

`android/app/src/main/java/cc/grepon/gatepath/GatepathApplication.kt` defines a
`BindWatchdog` `DefaultLifecycleObserver` that fires its lambda on `onStop`.
Verifying its delivery semantics (e.g. ON_PAUSE → ON_RESUME does NOT fire,
only ON_STOP does) requires `androidx.lifecycle:lifecycle-runtime` on the JVM
test classpath, which `run-jvm-tests.sh` does not currently provide.

**Mitigation in code:** `BindWatchdog` is `internal`, takes a lambda not a
`Context`, overrides only `onStop` (no `onPause`/`onResume`). Code review
catches a regression to per-Activity callbacks; this is the structural
guarantee until the test gap is closed.

**To close:** add `androidx.lifecycle:lifecycle-common-jvm:2.8.7` and
`lifecycle-runtime-jvm:2.8.7` from `https://dl.google.com/dl/android/maven2/`
to `run-jvm-tests.sh`'s `download_jar` block, then add a
`BindWatchdogTest.kt` that walks a `LifecycleRegistry` through ON_RESUME →
ON_PAUSE → ON_RESUME (must not fire) and ON_STOP (must fire once).

**Resolution:** unblocked when the test ships, or when `./gradlew :app:test`
(which has the dependency natively) replaces `run-jvm-tests.sh` in CI.

---

## Resolved

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
