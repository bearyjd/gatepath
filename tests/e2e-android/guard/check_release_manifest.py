#!/usr/bin/env python3
"""Guard: the no-leak test VPN apparatus must never ship.

Asserts the app's merged RELEASE manifest contains none of the markers below,
and the standalone `:testvpn` app's merged DEBUG manifest contains all of them
(positive control — proves the guard is actually looking at real manifests,
not vacuously passing). The test VPN no longer lives inside the app's own
debug source set — it is a separate application module so Gatepath is never
the VPN owner (see android/testvpn).

Usage: check_release_manifest.py <android dir>
Run after: ./gradlew :app:processReleaseManifest :testvpn:processDebugManifest
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKERS = ("GatepathTestVpnService", "BIND_VPN_SERVICE", "TestVpnControlActivity")

CONTROL_ACTIVITY = "TestVpnControlActivity"
CONTROL_ACTIVITY_PERMISSION = 'android:permission="android.permission.DUMP"'


def control_activity_export_failures(merged_debug_manifest: str) -> list[str]:
    """The control activity can start a packet-logging VPN, so it must stay
    reachable only by the shell uid: exported behind android.permission.DUMP
    (shell holds it, third-party apps cannot). A comment in
    android/testvpn/src/main/AndroidManifest.xml says so; this is the guard,
    because dropping the attribute would otherwise pass CI.
    """
    start = merged_debug_manifest.find(CONTROL_ACTIVITY)
    if start == -1:
        return []  # already reported by the marker loop above
    element_end = merged_debug_manifest.find(">", start)
    element = merged_debug_manifest[start:element_end if element_end != -1 else None]
    if CONTROL_ACTIVITY_PERMISSION in element:
        return []
    return [f"testvpn DEBUG manifest leaves {CONTROL_ACTIVITY} reachable by any app — it must carry {CONTROL_ACTIVITY_PERMISSION}"]


def merged_manifest(module_dir: Path, variant: str) -> Path:
    # AGP path varies by version; glob defensively for the variant's merged manifest.
    module_name = module_dir.name
    hits = sorted(module_dir.glob(f"build/intermediates/**/{variant}/**/AndroidManifest.xml"))
    hits = [h for h in hits if "merged" in str(h).lower()]
    if not hits:
        raise SystemExit(f"no merged manifest found for '{variant}' under {module_dir}/build")
    # Require the canonical task output (process{Variant}Manifest).  Do NOT fall back
    # to secondary intermediates like process{Variant}MainManifest — CI always runs the
    # exact task, so the canonical output must exist; guessing an intermediate is the
    # silent degradation this guard exists to prevent.
    variant_cap = variant.capitalize()
    task_seg = f"process{variant_cap}Manifest"
    canonical = sorted(h for h in hits if task_seg in str(h))
    if not canonical:
        raise SystemExit(
            f"no '{variant}' merged manifest from {task_seg} under {module_dir}/build"
            f" — run :{module_name}:{task_seg} first"
        )
    chosen = canonical[0]
    print(f"[guard] {variant}: using {chosen}", file=sys.stderr)
    return chosen


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_release_manifest.py <android dir>", file=sys.stderr)
        return 2
    android_dir = Path(argv[1])
    app_dir = android_dir / "app"
    testvpn_dir = android_dir / "testvpn"
    failures: list[str] = []

    release = merged_manifest(app_dir, "release").read_text()
    for m in MARKERS:
        if m in release:
            failures.append(f"RELEASE manifest leaks the test VPN marker: {m}")

    debug = merged_manifest(testvpn_dir, "debug").read_text()
    for m in MARKERS:
        if m not in debug:
            failures.append(
                f"testvpn DEBUG manifest unexpectedly missing {m} — guard may be vacuous"
            )
    failures.extend(control_activity_export_failures(debug))

    if failures:
        for f in failures:
            print(f"  ✗ {f}", file=sys.stderr)
        return 1
    print("  ✓ release manifest clean; debug manifest carries the apparatus")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
