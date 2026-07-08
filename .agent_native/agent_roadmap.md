# Gatepath — Agent-Native Readiness Roadmap

Audit date: 2026-07-07. Scope: can an AI coding agent pick up a raw bug report
or feature request and autonomously reproduce, implement, test, and verify it
with minimal human input?

**Headline finding:** this repo is unusually mature for this audit. It already
has honest, current docs (`docs/ROADMAP.md`, `docs/BLOCKERS.md`,
`docs/ARCHITECTURE.md`), two working e2e harnesses with host-side log
assertions, a captured tribal-knowledge skill
(`.omc/skills/android-e2e-captive-portal-expertise.md` /
`tests/e2e-android/HARNESS_NOTES.md`), and a schema-drift CI guard
(`schema-parity.yml`). The gaps are less "missing infrastructure" and more
"infrastructure that exists but isn't reachable from a cold agent context"
(no `CLAUDE.md` existed before this audit) plus a few genuine test-coverage
holes that are already tracked but open.

Items are ranked by **Human-Attention-Saved per Unit of Effort** — cheap
changes that remove a human from the loop rank above expensive ones that
remove the same human.

---

## Top 5 — immediately actionable

**Status (2026-07-07 follow-up pass):** items 1, 2, and 4 were already
satisfied by `CLAUDE.md` as shipped with this audit. Item 3 (NM
`Ip4Connectivity` dbusmock coverage) is now **done** — see its section
below; it also caught and fixed a live bug, not just a hypothetical gap.
Item 5 (`run-gatepath-fable.sh`) is now **done** via option (b) — rewritten,
not deleted.

### 1. Ship `CLAUDE.md` at repo root (this audit's companion deliverable)
**Effort:** done as part of this audit. **Saves:** every future agent session
re-deriving build commands, the mockportal contract, and the three-authority
captive-portal trap from scratch (previously buried in a `.omc/skills/*.md`
file and a `HARNESS_NOTES.md` an agent has no reason to open unprompted).
**Acceptance criteria:** `CLAUDE.md` exists at repo root; every command in it
was verified against an actual file in the repo (no invented Gradle tasks);
it cross-references `docs/BLOCKERS.md`, `docs/ROADMAP.md`, and
`tests/e2e-android/HARNESS_NOTES.md` instead of duplicating them wholesale.

### 2. Add a symptom-to-harness triage table
**Effort:** ~30 min, doc-only. **Saves:** the single biggest cold-start
ambiguity — this repo has **four** test layers (`android/run-jvm-tests.sh`,
`tests/e2e-android/`, `tests/e2e-docker/`, `tests/e2e-hwsim/`) with wildly
different privilege/hardware requirements (none / KVM+Docker / Docker only /
root+real-or-virtual-radio, **cannot run in a sandboxed agent environment at
all**). A raw bug report like "captive portal login doesn't validate on
Android" gives no hint which harness reproduces it, and picking wrong (e.g.
attempting `e2e-hwsim` in a container) wastes a full cycle on a harness that
structurally cannot succeed there.
**Acceptance criteria:** a table (now included in `CLAUDE.md`) mapping
symptom keywords → correct harness → exact command → whether it's
sandbox-runnable, so an agent can route in one lookup instead of trial and
error. File: `CLAUDE.md` (§ "Which test harness for which bug").

### 3. Land the NetworkManager wire-contract dbusmock test (`docs/BLOCKERS.md` "Testing gap") — DONE 2026-07-07
**Turned out to be a live bug, not just a coverage gap:** while landing this
test, found that `desktop/gatepath/portal_monitor.py` (the Python desktop
client) was reading the bare `Connectivity` property — the exact typo'd/
stale-rename scenario this item warned about, just on the Python side
instead of Rust (the Rust helper's `network_manager.rs` was already
correct). Fixed `portal_monitor.py` to read `Ip4Connectivity`. Added
`desktop/tests/test_nm_property_contract.py` (dependency-free — no real
D-Bus needed, runs everywhere; verified RED against the pre-fix code and
GREEN after) and `desktop/tests/test_nm_dbusmock_connectivity.py` (the
`python-dbusmock`-backed integration test this item specified, using
`DBusTestCase.start_system_bus()` for a private, non-root bus; skips
cleanly when `python-dbusmock`/`dasbus`/`dbus-daemon` aren't installed).
Wired `python-dbusmock`, `dasbus`, and `PyGObject` (plus the system libs
PyGObject needs to build) into `.github/workflows/desktop.yml`'s `pytest`
job so the dbusmock test runs for real in CI — **this CI wiring is
unverified in this sandbox** (no GitHub Actions runner available here); the
dependency-free test and the full `desktop/` + `mockportal/` suite (268
passed, 1 skipped) were verified directly. See `docs/BLOCKERS.md`
BLOCKER-DESK-004 for the full resolution writeup, including the one
follow-up it flags (no equivalent non-privileged coverage yet for the
Rust-side read, since it needed no fix this round).

**Effort:** ~half a day (one new `python-dbusmock`-backed test file under
`desktop/tests/`). **Saves:** this is the one place the repo's own docs admit
an agent-introduced regression would ship silently: `network_manager.rs`
reads the NM property `Ip4Connectivity` and the *only* thing exercising that
literal string today is the privileged `tests/e2e-hwsim/` harness, which
cannot run in CI or in a sandboxed agent session. A typo'd rename would pass
every check that *can* run automatically.
**Acceptance criteria:** new test in `desktop/tests/` stands up a fake
NetworkManager on a private session bus (`python-dbusmock`) and asserts the
Python-side D-Bus client reads `Ip4Connectivity` and fails loudly (not
silently defaults) if that property is absent/renamed; runs under
`python -m pytest tests/` with no root/netns privilege; wired into the
existing `desktop.yml` or `desktop-e2e.yml` CI job. Closes the gap named in
`docs/BLOCKERS.md` under "Testing gap — NM connectivity wire-contract is not
covered in CI".

### 4. Make `driver/assertions.py`-style host-side assertions the documented default pattern for new e2e coverage
**Effort:** ~1 hour, doc-only (the code pattern already exists twice:
`tests/e2e-android/driver/assertions.py`, `tests/e2e-docker/driver/`). **Saves:**
prevents a regression this repo already fixed once — the harness reporting
`rc=0` on every scenario step while the *actual* security property (off-domain
blocking, audit-log content, no-leak confinement) silently failed. Without a
documented "assertions are a separate pass over artifacts, not scenario
step success" rule, an agent extending either harness is likely to fold a new
check into the scenario's own step list and lose the fail-hard property.
**Acceptance criteria:** `CLAUDE.md` states the rule explicitly with a
one-line example; no code change required — this is capturing an existing,
proven convention before it's lost to attrition.

### 5. Give `run-gatepath-fable.sh` a narrower, truthful scope or delete it — DONE 2026-07-07 (option b)
Rewrote the embedded prompt: dropped the invented Phase 1–5 feature list
(cert pinning, DNS guard rails, "Zero-Leak Verification"), pointed the agent
at `CLAUDE.md` + `.agent_native/agent_roadmap.md` first, scoped it to picking
up ONE already-tracked item instead of improvising features, and replaced
"you are explicitly authorized to push verified branches to the remote
repository" / self-merge-to-main with an explicit "do NOT push, do NOT
merge — open a PR and stop" instruction matching this repo's real review
convention. Also dropped `--dangerously-skip-permissions` and the infinite
retry loop, since a scoped, review-gated prompt undermines its own point if
still run with permission checks off. Verified `bash -n` syntax-checks
clean; the `claude -p` invocation itself wasn't executed (that's launching
a new agent session, out of scope for this pass).

**Effort:** ~15 min decision + edit. **Saves:** this script is the one
document in the repo that actively **misleads** an agent: it's a `claude -p`
wrapper that instructs a fresh agent to autonomously implement
certificate-pinning / DNS-guard-rail / "Zero-Leak Verification" features that
don't exist yet, and — critically — tells it "you are explicitly authorized
to push verified branches to the remote repository" and to merge to `main`
itself, which conflicts with this repo's actual, working convention (PRs +
separate code/security review, visible in every recent commit). Any agent
that gets pointed at this script inherits a mandate to bypass the review
process the rest of the repo depends on.
**Acceptance criteria:** either (a) delete `run-gatepath-fable.sh` and its
already-present `.gitignore` entry, or (b) rewrite its embedded prompt to
match the repo's real state (no invented Phase 1–5 feature list) and remove
the self-authorized push/merge instruction, requiring a human-reviewed PR
like every other change in this repo.

---

## Audit area 1 — Human-judgment chokepoints

The highest-value tribal knowledge is already written down, just not
CLAUDE.md-visible: the three-authority captive-portal race
(`.omc/skills/android-e2e-captive-portal-expertise.md`,
`tests/e2e-android/HARNESS_NOTES.md`) where the OS's captive-URL setting,
Gatepath's own hardcoded-to-gstatic probe, and the mock's login-gated
`/generate_204` must all agree or the flow silently short-circuits with
`rc=0` and passing scenario steps. Equally load-bearing: the system
"Sign in to network" notification is provably untappable on a headless
emulator (ANRs SystemUI), so dispatch goes through a `BuildConfig.DEBUG`
intent instead — an agent that doesn't know this will burn cycles trying to
drive the notification. Smaller but real: the GrapheneOS quirk
(`docs/TESTING_ANDROID.md` — NetworkStack hardcodes probe URLs, ignoring
`settings put global captive_portal_http_url` entirely) and the AGP-9
built-in-Kotlin gotcha (`android/app/build.gradle.kts` — applying
`kotlin("android")` hard-fails the build). All of these are now surfaced in
`CLAUDE.md` rather than left to be rediscovered.

## Audit area 2 — Verification gaps

Two gaps are explicitly named in the repo's own `docs/BLOCKERS.md` and both
are real: the NM `Ip4Connectivity` wire-contract has zero CI coverage (item 3
above), and secured (WPA2-PSK) captive networks are modelled but return
`ConnectivityError::Unsupported` with no test proving that refusal is a
*clean* refusal rather than a silent hang. Beyond what's already tracked: the
repo's self-verification story is strong for the two Docker-based harnesses
(`driver/assertions.py`, `AuditSchemaParityTest.kt`, the drift guard for
`RefusalReason` in `test_netns_client.py`) but there is no equivalent
artifact-based assertion layer for `tests/e2e-hwsim/` — it's inherently
un-runnable in CI or by an agent, so its "proof" currently lives only in prose
(`docs/ROADMAP.md` P0.2, handoff notes) rather than a machine-checkable
artifact a future agent could diff against.

## Audit area 3 — Reproduction paths

`mockportal/server.py` is a genuinely good reproduction fixture: it models
real captive semantics (redirect-until-login, then 204 forever), is
parameterized for both the fast desktop path (`PORTAL_COMPLETE_AFTER=3`) and
the must-stay-captive Android path (`=1000`), and has a purpose-built
leak-sentinel injection mode (`PORTAL_LEAK_SENTINEL`) for the no-leak proof.
The gap is not fixture coverage — it's **harness selection**: given a bug
report, nothing routes an agent to the right one of four reproduction paths
with four different privilege models (item 2 above closes this cheaply).
Two reproduction paths are permanently out of reach and correctly documented
as such rather than silently gapped: GrapheneOS devices (hardcoded probe
URLs) and secured captive Wi-Fi (netns helper doesn't attempt it) — no action
needed beyond keeping that framing honest.

## Audit area 4 — Structural obstacles

No meaningful entanglement found. Android and desktop deliberately share no
code (documented rationale in `docs/ARCHITECTURE.md`); the only cross-language
coupling is the audit-log JSON schema and the `RefusalReason` enum, and both
already have CI drift guards (`schema-parity.yml`,
`test_netns_client.py::test_python_refusal_reasons_cover_every_rust_variant`)
that were purpose-built after a real regression (#51) — this is the pattern
to imitate, not a gap to fix. The Android `diag/` package is already
decomposed into small, single-responsibility files consistent with this
project's own style. The closest thing to a structural obstacle is the
**test-harness proliferation** itself (four layers, four privilege models,
no shared entrypoint) — addressed as a routing problem (item 2), not a code
boundary problem, since each harness's isolation is deliberate and correct
for what it tests.

---

## Files written by this audit
- `.agent_native/agent_roadmap.md` (this file)
- `CLAUDE.md` (repo root — new)
