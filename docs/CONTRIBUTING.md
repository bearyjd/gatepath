# Contributing

Gatepath is **two independent apps that share no code** — `android/` (Kotlin /
Jetpack Compose / Hilt) and `desktop/` (Python 3.11+ / GTK4 / WebKit2GTK) plus
`desktop/gatepath-netns-helper/` (Rust, privileged netns helper) — joined only by
an audit-log schema and a security model. `mockportal/` is a shared, stdlib-only
mock captive portal used by every test layer.

Read first, in order: [`SECURITY_MODEL.md`](SECURITY_MODEL.md) (what this
protects, per platform), [`ARCHITECTURE.md`](ARCHITECTURE.md) (why two apps),
[`ROADMAP.md`](ROADMAP.md) (proven vs. claimed), [`BLOCKERS.md`](BLOCKERS.md)
(open/resolved build-env issues — check here before assuming something is broken).

## Build & test

The **canonical, verified** build/test commands for each area live in
[`../CLAUDE.md`](../CLAUDE.md) ("Build & test — verified commands only") — run
from there rather than trusting a copy that can drift. In short:

| Area | Entry point |
|------|-------------|
| Android (with SDK) | `cd android && ./gradlew :app:test :app:assembleDebug` |
| Android (no SDK — JVM logic subset) | `bash android/run-jvm-tests.sh` (JDK 21 + kotlinc 2.0.x + python3) |
| Desktop (Python) | `cd desktop && python -m pytest tests/` |
| Rust netns helper | `cd desktop/gatepath-netns-helper && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test` |
| Mock portal | `python -m pytest mockportal/` |

CLAUDE.md's "Which test harness for which bug" table maps a symptom to the right
one of the **four** e2e/unit layers (some need `/dev/kvm`, real root + netns, or a
physical device and structurally cannot run in a sandbox) — consult it before
reaching for a Docker/emulator harness; most business-logic bugs only need the
JVM/pytest unit layer + `mockportal/` fixtures.

Repo conventions worth knowing before you touch code:

- **Small, single-responsibility files** are the norm (see `android/.../diag/`).
- **`unsafe_code = "deny"`** at the Rust crate level — no `unsafe` without an
  explicit, reviewed reason.
- **Cross-language contracts get a machine-checked drift guard, not a comment**
  (precedents: `schema-parity.yml`, `dbus-contract-parity.yml`, the
  refusal-reason and cause-parity source-parsing tests). Add one when you
  introduce a new cross-language enum or schema.
- The five privileged-boundary validators are covered by **in-CI `proptest`
  suites _and_ out-of-CI `cargo-fuzz` targets** (`desktop/gatepath-netns-helper/fuzz/`,
  nightly); change a validator → update both.

## PR workflow

- Changes land as **reviewed PRs** — do **not** self-merge or push directly to
  `main`. Every recent commit on `main` follows this.
- Conventional-commit subjects (`feat:`, `fix:`, `test:`, `ci:`, `docs:`, …).
- Keep a PR scoped to one logical unit of work; the squashed merge message
  should tell the whole story.

### Fix-up PRs

When a code review surfaces a HIGH-severity finding on a PR that hasn't been
merged yet, push the fix as a follow-up commit to the **same branch** rather than
opening a separate PR. Squash-merge the bundled PR.

**Why**: separate fix-up PRs duplicate review/CI/merge cycles, scatter the same
logical change across two `git log` entries, and force reviewers to
context-switch. Bundling keeps history aligned with logical units of work — the
squashed merge message tells the whole story, and reverts remain atomic per merge.

**Open a separate PR only when** the follow-up:

- adds *new* scope unrelated to the original review
- needs to land on a different timeline (e.g. blocked on review while the
  original is shippable)
- must be revertible independently of the original change

This applies to AI-generated fixes too — when a `/code-review` cycle finds a HIGH
issue, push the fix to the existing branch instead of branching off main again.

## Pre-merge checklist

Before requesting review, ensure:

- CI is green for the areas you touched — JVM unit tests, `assembleDebug`,
  `assembleRelease`, and schema/contract parity for Android; `pytest` for the
  desktop app + mockportal; `cargo fmt --check` / `clippy -D warnings` / `test`
  for the Rust helper.
- The relevant local suite passes (e.g. `bash android/run-jvm-tests.sh` for the
  Android JVM subset; `python -m pytest desktop/ mockportal/` for desktop).
- The branch is rebased on `origin/main`.
- If you closed something [`ROADMAP.md`](ROADMAP.md) or [`BLOCKERS.md`](BLOCKERS.md)
  tracks as open, update it — **only after a real, verified run** (these are
  living, honest status docs).

## Release builds

CI runs `assembleRelease` on every PR and uploads the **unsigned** APK as an
artifact. This catches R8 minify regressions, missing ProGuard keep rules for
Hilt/serialization, and resource-shrink mistakes at PR time rather than ship time.
The PR-CI artifact is never signed.

Signing happens only on a real `v*` tag, in `release.yml` (see
[`RELEASING.md`](RELEASING.md)): every published artifact — the Android AAB/APK,
the SBOM, and the desktop sysext `.raw` + Flatpak bundle — gets **keyless cosign
provenance** (Sigstore, no long-lived keys), and the Android artifacts are
**additionally** keystore-signed *if* the maintainer's `ANDROID_*` secrets are
configured (optional; unset ⇒ artifacts are clearly labelled unsigned). Neither
signing path runs on PR CI — the cosign OIDC flow needs a live tag run.
