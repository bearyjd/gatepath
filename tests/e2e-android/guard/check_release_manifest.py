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
    # Require the canonical task output (process{Variant}Manifest).  Do NOT fall back
    # to secondary intermediates like process{Variant}MainManifest — CI always runs the
    # exact task, so the canonical output must exist; guessing an intermediate is the
    # silent degradation this guard exists to prevent.
    variant_cap = variant.capitalize()
    task_seg = f"process{variant_cap}Manifest"
    canonical = sorted(h for h in hits if task_seg in str(h))
    if not canonical:
        raise SystemExit(
            f"no '{variant}' merged manifest from {task_seg} under {app_dir}/build"
            f" — run :app:{task_seg} first"
        )
    chosen = canonical[0]
    print(f"[guard] {variant}: using {chosen}", file=sys.stderr)
    return chosen


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
