# Distribution (store submission drafts)

Draft recipes for the two FOSS stores. **Neither is submitted yet** (verified
2026-07-01: no `fdroiddata` recipe, no Flathub repo, no listing anywhere). These
files are staged here so they're ready to copy into the external repos when the
project decides to publish. They are **not** built by this repo's CI.

| Store | Draft | Destination |
|-------|-------|-------------|
| F-Droid | [`fdroid/com.ventouxlabs.gatepath.yml`](fdroid/com.ventouxlabs.gatepath.yml) | `fdroiddata:metadata/com.ventouxlabs.gatepath.yml` |
| Flathub | [`flathub/com.ventouxlabs.Gatepath.yml`](flathub/com.ventouxlabs.Gatepath.yml) | PR to `flathub/flathub` → `flathub/com.ventouxlabs.Gatepath` |

App ids (post-rebrand, see the `identity-rename` history): Android/F-Droid
`com.ventouxlabs.gatepath` (lowercase), desktop/Flathub `com.ventouxlabs.Gatepath`
(capital G).

## Prerequisites common to both

1. ~~**Add a `LICENSE` file.**~~ **Done** — the canonical GPL-3.0 text ships at
   the repo root as `LICENSE` (the declared license is `GPL-3.0-or-later`).
2. **Refresh release pins.** The F-Droid draft still targets `v1.0.0` /
   versionCode 1, while current Android metadata is `1.1.0`. The Flathub draft
   uses a placeholder commit. Select an actual release tag, align versions with
   that tag's source, and pin its full commit before submission.

## F-Droid

1. (Optional) file a Request-For-Packaging at <https://gitlab.com/fdroid/rfp>.
2. Add the recipe to `fdroiddata` and open a merge request.
3. F-Droid **builds from source and signs with its own key** — it ignores the
   Play keystore. `build.gradle.kts` produces an unsigned release APK when the
   `ANDROID_*` env vars are absent (F-Droid's case), which F-Droid then signs.
4. Store text (title/descriptions/changelogs) lives at the repo root,
   `fastlane/metadata/android/en-US/` — one of the layouts F-Droid's importer
   scans (`<repo>/fastlane/...`), so it is auto-imported. Confirm during
   `fdroid build`/`fdroid lint`.
5. `subdir: android/app` with `gradle: [yes]`. The gradle wrapper + settings live
   in `android/` (not repo root); confirm the buildserver locates the wrapper.

## Flathub

1. Fork `flathub/flathub`, add `com.ventouxlabs.Gatepath.yml` at the repo root,
   open a PR; on merge Flathub creates the per-app repo.
2. The manifest mirrors the CI-built dev manifest
   (`desktop/com.ventouxlabs.Gatepath.yml`); the only change is a pinned
   `type: git` tag+commit source instead of the local `dir` path.
3. **Host isolation dependency.** Isolation is done by
   a root **system** D-Bus service (`com.ventouxlabs.Gatepath.NetNsHelper`) that
   cannot live inside a Flatpak sandbox. Confined sign-in requires
   the helper and portal runtime installed on the host and the manifest grants
   `--system-talk-name=com.ventouxlabs.Gatepath.NetNsHelper` (commented in the
   draft, enabled in the development/CI manifest). The complete native RPM
   provides the app/runner/helper and required GUI stack; the sysext needs the
   host app/runtime separately. Without the helper the app supports unconfined
   fallback sign-in with a VPN warning, rather than namespace isolation.
   Resolve the draft's grant and document this behavior before submission.

## Status

Both are **drafts with `TODO`s** (release/version pins, commit sha, helper talk-name —
LICENSE and the fastlane path are resolved). Treat them as a starting point to
validate with `fdroid build` / `flatpak-builder`, not as submit-ready.
