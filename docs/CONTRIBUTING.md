# Contributing

## Fix-up PRs

When a code review surfaces a HIGH-severity finding on a PR that hasn't been merged yet, push the fix as a follow-up commit to the **same branch** rather than opening a separate PR. Squash-merge the bundled PR.

**Why**: separate fix-up PRs duplicate review/CI/merge cycles, scatter the same logical change across two `git log` entries, and force reviewers to context-switch. Bundling keeps history aligned with logical units of work — the squashed merge message tells the whole story, and reverts remain atomic per merge.

**Open a separate PR only when** the follow-up:

- adds *new* scope unrelated to the original review
- needs to land on a different timeline (e.g. blocked on review while the original is shippable)
- must be revertible independently of the original change

This applies to AI-generated fixes too — when a `/code-review` cycle finds a HIGH issue, push the fix to the existing branch instead of branching off main again.

## Pre-merge checklist

Before requesting review, ensure:

- All CI checks pass (JVM unit tests, `assembleDebug`, `assembleRelease`, schema parity)
- Local `bash android/run-jvm-tests.sh` is green
- Branch is rebased on `origin/main`

## Release builds

CI runs `assembleRelease` on every PR and uploads the unsigned APK as an artifact. This catches R8 minify regressions, missing ProGuard keep rules for Hilt/serialization, and resource-shrink mistakes at PR time rather than at ship time.

The release APK is **unsigned** — signing is a manual step performed only at ship time, not in CI.
