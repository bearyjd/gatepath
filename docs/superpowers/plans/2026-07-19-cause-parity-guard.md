# PR 5 — Cross-platform cause-parity drift guard + docs

**Date:** 2026-07-19
**Spec:** `docs/superpowers/specs/2026-07-18-diagnostics-expansion-design.md` (§Testing "Parity guard"; PR sequencing #5)
**Stacks on:** #83 (desktop diagnosis panel, merged `04c9890`)
**Closes:** the diagnostics-expansion sequence (#79/#80/#81/#82/#83 + this).

## Why

The Android `DiagnosticReport` sealed interface and the desktop `Cause` enum are
two hand-maintained copies of one vocabulary. Repo convention (`CLAUDE.md`):
cross-language contracts get a **drift guard, not a comment** — precedent
`schema-parity.yml` (audit-log schema) and
`test_netns_client.py::test_python_refusal_reasons_cover_every_rust_variant`
(parses Rust source, round-trips every wire name). This adds the equivalent
guard for the diagnostic cause vocabulary, and updates the two docs that now
describe a shipped-on-both-platforms feature.

## The contract (verified against source this session)

**Kotlin** `android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticReport.kt` —
sealed interface with **12** variants (`data object`/`data class ... : DiagnosticReport`):
`Healthy, VpnBlocking, DnsHijack, PrivateDnsBlocking, HttpProxyBlocking,
SandboxedWebView, HttpsOnlyCaptive, CellularFallback, NoDnsServers,
PortalRedirectLoop, ClockSkew, Inconclusive`.

**Python** `desktop/gatepath/diag/report.py` — `Cause` enum, **9** values whose
`.value` strings are spelled exactly as the Kotlin variant names:
`Healthy, VpnBlocking, DnsHijack, HttpProxyBlocking, HttpsOnlyCaptive,
NoDnsServers, PortalRedirectLoop, ClockSkew, Inconclusive`.

**Android-only allowlist** (3): `PrivateDnsBlocking`, `SandboxedWebView`,
`CellularFallback` — documented in `report.py`'s module docstring. 12 − 3 = 9.
No desktop-only causes exist today.

## Tasks

### Task 1 — the parity guard (TDD)
New `desktop/tests/test_cause_parity.py`. Mirror the precedent's shape
(`test_netns_client.py`): lightweight regex over checked-in source, narrowed to
the relevant block, with a loud assertion if the source shape moves.

- `_kotlin_report_variants() -> set[str]`: read the Kotlin file
  (`Path(__file__).resolve().parents[2] / "android" / ... / "DiagnosticReport.kt"`);
  parse variant names declared as `data object <Name> : DiagnosticReport` and
  `data class <Name>(...) : DiagnosticReport`. Must capture all 12 and must NOT
  capture the enclosing `sealed interface DiagnosticReport`. `assert` the file
  parsed to a non-empty, plausibly-sized set with a message naming the file if
  the shape changed (per precedent).
- `_python_cause_values() -> set[str]`: the `.value` of every `Cause` member
  (import `gatepath.diag.report.Cause`, don't re-parse — it's Python).
- `_ANDROID_ONLY = {"PrivateDnsBlocking", "SandboxedWebView", "CellularFallback"}`
  as an explicit module constant, with a comment pointing at `report.py`'s
  docstring as the source of truth for *why* each is Android-only.
- Tests:
  1. **`kotlin − allowlist == python`** — the core parity assertion, with a diff
     message showing symmetric difference on failure.
  2. **every allowlist name is an actual Kotlin variant** — so the allowlist
     can't silently reference a renamed/deleted variant (which would hide a real
     drift).
  3. **no desktop-only cause** — `python ⊆ kotlin` (redundant with #1 today, but
     pins the "vice versa" direction the spec calls for, so a future desktop-only
     cause forces an explicit allowlist decision rather than a silent pass).
  4. **counts pin**: assert `len(kotlin) == 12` and `len(python) == 9` with a
     message explaining the arithmetic (12 − 3 allowlist = 9), so *adding* a
     Kotlin variant without touching either side fails loudly.
- Runs in the existing `pytest desktop + mockportal` CI job (monorepo checkout —
  `android/` source is present). No new workflow needed; matches the
  refusal-reasons precedent which likewise reads sibling-tree source.

### Task 2 — docs
- **`docs/TROUBLESHOOTING.md`**: the in-app diagnosis panel now ships on **both**
  platforms (Android `MainScreen`, desktop "Run diagnostics" button). Update the
  section that currently says "the 9 Android `DiagnosticEngine` findings" to the
  expanded **12-cause** vocabulary, note which 3 are Android-only and why
  (Private DNS / sandboxed WebView / cellular fallback are Android concepts),
  and mention the desktop mirror is manual-run today.
- **`docs/ROADMAP.md`**: mark the diagnostics-expansion sequence complete
  (#79/#80/#82/#83 + this parity guard). Reference the new guard alongside
  `schema-parity.yml` and the refusal-reasons test as the third cross-language
  drift guard. Do **not** claim the still-open "bigger drift guard" (P1.1 shared
  schema) is closed — this is a source-parsing guard, same tier as the
  refusal-reasons one, not the shared-schema upgrade.

## Review gates
Independent reviewer on the diff. Specifically check: the Kotlin parser captures
exactly 12 (not 11, not 13, not the interface itself), fails loudly if the file
moves/renames, and the allowlist can't go stale. Fact-check the doc prose
(cause counts, which are Android-only) against `report.py` and
`DiagnosticReport.kt` — per the SECURITY_MODEL blind-spot lesson, doc claims
about code get verified like code.

## Definition of done
- `pytest tests/test_cause_parity.py` green; guard fails loudly if either side
  drifts (verify by a temporary local edit, then revert).
- Full `pytest tests/` green; ruff clean on the new file.
- Docs accurate against source.
- Lands as a reviewed PR (no direct push to `main`).
