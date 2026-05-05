# Gatepath — Build Blockers

This file tracks issues that block parts of the MVP from being verified end-to-end in
the build environment. Each entry should name the file, the symptom, the diagnosis, and
the workaround (if any).

## Status

No active blockers.

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
