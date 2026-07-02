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

1. **Add a `LICENSE` file.** The repo declares `GPL-3.0-or-later` (Android
   `build.gradle`/metainfo) but ships no license text. Both stores require it.
2. **Cut release tags.** F-Droid builds from tag `v1.0.0`; Flathub pins a tag +
   full commit sha. No release tag exists yet.

## F-Droid

1. (Optional) file a Request-For-Packaging at <https://gitlab.com/fdroid/rfp>.
2. Add the recipe to `fdroiddata` and open a merge request.
3. F-Droid **builds from source and signs with its own key** — it ignores the
   Play keystore. `build.gradle.kts` produces an unsigned release APK when the
   `ANDROID_*` env vars are absent (F-Droid's case), which F-Droid then signs.
4. Store text (title/descriptions/changelogs) already lives at
   `android/fastlane/metadata/android/en-US/`. **Caveat:** F-Droid scans
   `<repo>/fastlane/...` or `<subdir>/src/<flavor>/fastlane/...`; our copy is at
   `android/fastlane/...`, so it likely won't be auto-imported without moving it
   or adjusting the layout. Confirm during `fdroid build`/`fdroid lint`.
5. `subdir: android/app` with `gradle: [yes]`. The gradle wrapper + settings live
   in `android/` (not repo root); confirm the buildserver locates the wrapper.

## Flathub

1. Fork `flathub/flathub`, add `com.ventouxlabs.Gatepath.yml` at the repo root,
   open a PR; on merge Flathub creates the per-app repo.
2. The manifest mirrors the CI-built dev manifest
   (`desktop/com.ventouxlabs.Gatepath.yml`); the only change is a pinned
   `type: git` tag+commit source instead of the local `dir` path.
3. **Open architectural question — the privileged helper.** Isolation is done by
   a root **system** D-Bus service (`com.ventouxlabs.Gatepath.NetNsHelper`) that
   cannot live inside a Flatpak sandbox. From Flatpak the app only functions if
   the helper is installed on the host (sysext/RPM) and the manifest grants
   `--system-talk-name=com.ventouxlabs.Gatepath.NetNsHelper` (commented in the
   draft). Until that host-dependency story is packaged/documented, a Flathub
   build is effectively GUI-only. Resolve before submitting.

## Status

Both are **drafts with `TODO`s** (release tag, commit sha, LICENSE, fastlane
path, helper talk-name). Treat them as a starting point to validate with
`fdroid build` / `flatpak-builder`, not as submit-ready.
