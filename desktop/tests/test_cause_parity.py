"""Cross-platform cause-parity drift guard (diagnostics-expansion PR 5).

The Android `DiagnosticReport` sealed interface and the desktop `Cause` enum are
two hand-maintained copies of one diagnostic-cause vocabulary. Repo convention
(`CLAUDE.md`): a cross-language contract gets a **drift guard, not a comment**.
Precedent: `schema-parity.yml` (audit-log schema) and
`test_netns_client.py::test_python_round_trips_every_helper_wire_error` (parses
the Rust source, round-trips every wire name). This is the third such guard —
same tier as the refusal-reasons one: a lightweight source parser, NOT the
shared-schema upgrade tracked in ROADMAP P1.1.

Kotlin is the source of truth (12 variants); desktop mirrors it minus two
causes that are Android platform concepts. If either side drifts, the parity
tests below fail loudly and name what moved.
"""

from __future__ import annotations

import re
from pathlib import Path

from gatepath.diag.report import Cause

# Android sealed interface — the source of truth for the vocabulary. Sits in the
# sibling `android/` tree, present in the monorepo checkout the pytest CI job runs.
_KOTLIN_REPORT = (
    Path(__file__).resolve().parents[2]
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "ventouxlabs"
    / "gatepath"
    / "diag"
    / "DiagnosticReport.kt"
)

# Causes that legitimately exist only on Android — see `gatepath/diag/report.py`'s
# module docstring for the per-cause rationale (the sandboxed-WebView process
# model is Android-specific, and desktop has no cellular radio to fall back
# onto). This allowlist is the ONLY sanctioned asymmetry; test 2 pins it against
# real Kotlin variants so it can't go stale. (`PrivateDnsBlocking` was here too
# until desktop gained systemd-resolved strict-DoT detection.)
_ANDROID_ONLY = {"SandboxedWebView", "CellularFallback"}


# A `data object`/`data class <Name>` declaration ending in `: DiagnosticReport`.
# The optional param group tolerates ONE level of nested parens
# (`(?:[^()]|\([^()]*\))*`) so a data-class param default like
# `val tags: List<String> = listOf()` or a function-type param `(String) -> Unit`
# does NOT truncate the match at the inner `)` — which would silently DROP that
# variant from the parse and let the guard go green on exactly the drift it
# exists to catch (a `)`-param cause added to Android but not desktop). Consuming
# the param list as a unit also means a field typed `DiagnosticReport` inside the
# params can't be mistaken for the supertype anchor.
_VARIANT_RE = re.compile(
    r"data (?:object|class)\s+(\w+)\s*"
    r"(?:\((?:[^()]|\([^()]*\))*\))?\s*"
    r":\s*DiagnosticReport",
    re.DOTALL,
)


def _parse_report_variants(interface_body: str) -> set[str]:
    """Variant names from a `sealed interface DiagnosticReport` body.

    Pure (text in, names out) so the regex robustness is unit-testable against
    synthetic snippets without editing the real Kotlin file — see
    `test_parser_tolerates_parens_in_param_lists`.
    """
    return set(_VARIANT_RE.findall(interface_body))


def _kotlin_report_variants() -> set[str]:
    """Variant names of the Kotlin `sealed interface DiagnosticReport`.

    Reads the checked-in source, narrows to the interface body, and parses the
    `data object/class <Name> ... : DiagnosticReport` declarations. Deliberately
    lightweight (per the refusal-reasons precedent); the heavier, more robust
    pattern is a shared checked-in schema both languages validate against — see
    `schema-parity.yml` and ROADMAP P1.1.
    """
    text = _KOTLIN_REPORT.read_text(encoding="utf-8")
    # Narrow to the interface body so nothing outside it can leak in, and so the
    # enclosing `sealed interface DiagnosticReport` header is excluded by
    # construction (it is not a `data object`/`data class`).
    body = re.search(
        r"sealed interface DiagnosticReport\s*\{(.*)\}", text, re.DOTALL
    )
    assert body, (
        f"could not locate `sealed interface DiagnosticReport {{ … }}` in "
        f"{_KOTLIN_REPORT} — did the interface get renamed, split, or move out of "
        f"this file? Update this parser (or migrate to a shared schema; see "
        f"schema-parity.yml)."
    )
    variants = _parse_report_variants(body.group(1))
    assert variants, (
        f"parsed zero variants from {_KOTLIN_REPORT} — the `data object/class "
        f"<Name> : DiagnosticReport` declaration shape moved. Update this parser."
    )
    return variants


def _python_cause_values() -> set[str]:
    """The wire spelling of every desktop `Cause` member (its `.value`)."""
    return {c.value for c in Cause}


def test_parser_tolerates_parens_in_param_lists() -> None:
    """A `)` inside a data-class param list must NOT drop the variant.

    This is the exact silent-under-cover failure the guard cannot afford: if the
    parser stopped at the first `)`, a future Android cause with a param default
    like `listOf()` or a function-type param would vanish from the parse, the
    count would still read 12, and the guard would go green on real drift. Pinned
    against a synthetic snippet so it survives without touching the real file.
    """
    snippet = """
        data object Plain : DiagnosticReport

        data class WithDefaultCall(
            val tags: List<String> = listOf(),
        ) : DiagnosticReport

        data class WithFunctionParam(
            val onDone: (String) -> Unit,
        ) : DiagnosticReport

        data class WithNestedType(
            val related: DiagnosticReport,
            val n: Int,
        ) : DiagnosticReport
    """
    assert _parse_report_variants(snippet) == {
        "Plain",
        "WithDefaultCall",
        "WithFunctionParam",
        "WithNestedType",
    }


def test_kotlin_minus_android_only_equals_python() -> None:
    """The core parity assertion: desktop mirrors Kotlin minus the allowlist.

    If a shared cause is added/renamed on one side but not the other, the
    symmetric difference below names exactly what drifted.
    """
    kotlin = _kotlin_report_variants()
    python = _python_cause_values()
    expected = kotlin - _ANDROID_ONLY
    assert expected == python, (
        "diagnostic cause vocabulary drifted between Android and desktop.\n"
        f"  Kotlin variants:        {sorted(kotlin)}\n"
        f"  Android-only allowlist: {sorted(_ANDROID_ONLY)}\n"
        f"  expected desktop set:   {sorted(expected)}\n"
        f"  actual desktop Cause:   {sorted(python)}\n"
        f"  symmetric difference:   {sorted(expected ^ python)}\n"
        "Reconcile DiagnosticReport.kt and gatepath/diag/report.py "
        "(and update _ANDROID_ONLY if a new asymmetry is intended)."
    )


def test_android_only_allowlist_names_real_kotlin_variants() -> None:
    """The allowlist can't reference a renamed/deleted variant.

    Otherwise a real drift (a Kotlin variant quietly renamed) could be masked by
    a stale allowlist entry that no longer matches anything.
    """
    kotlin = _kotlin_report_variants()
    stale = _ANDROID_ONLY - kotlin
    assert not stale, (
        f"_ANDROID_ONLY references names that are no longer Kotlin variants: "
        f"{sorted(stale)}. A DiagnosticReport variant was renamed or removed — "
        "fix the allowlist (and confirm desktop parity is still correct)."
    )


def test_no_desktop_only_cause() -> None:
    """Pin the reverse direction: every desktop cause exists on Android.

    Redundant with the core test today, but it forces a future desktop-only
    cause to be an explicit allowlist decision rather than a silent pass.
    """
    kotlin = _kotlin_report_variants()
    python = _python_cause_values()
    orphans = python - kotlin
    assert not orphans, (
        f"desktop `Cause` has values with no Kotlin variant: {sorted(orphans)}. "
        "Add the variant to DiagnosticReport.kt, or (if desktop-only is intended) "
        "introduce a documented desktop-only allowlist mirroring _ANDROID_ONLY."
    )


def test_variant_counts_are_pinned() -> None:
    """12 Kotlin − 2 Android-only = 10 desktop.

    A blunt count check so *adding* a Kotlin variant (without touching either the
    desktop enum or the allowlist) fails here too, not only via the set diff.
    """
    kotlin = _kotlin_report_variants()
    python = _python_cause_values()
    assert len(kotlin) == 12, (
        f"expected 12 Kotlin DiagnosticReport variants, found {len(kotlin)}: "
        f"{sorted(kotlin)}. If the vocabulary changed on purpose, update this "
        "count and reconcile the desktop side + docs/TROUBLESHOOTING.md."
    )
    assert len(python) == 10, (
        f"expected 10 desktop Cause values (12 Kotlin − 2 Android-only), found "
        f"{len(python)}: {sorted(python)}."
    )
