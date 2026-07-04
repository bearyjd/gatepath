# Releasing Gatepath (Android)

The Android release pipeline (ROADMAP P2.2) is **tag-triggered**: pushing a tag
like `v1.0.0` runs `.github/workflows/release.yml`, which builds the release
**AAB** (Play) + **APK**, generates a CycloneDX **SBOM**, and publishes a GitHub
Release with all three.

Signing is **optional** but required for a real release. The pipeline runs
without a keystore (producing clearly-labelled **unsigned** artifacts), so CI
never breaks; configure the secrets below to produce **signed** artifacts.

> The signing keystore is **never** stored in the repo. Keep it and its
> passwords somewhere safe and backed up: losing the key means you can no longer
> ship updates to an existing Play listing. (F-Droid is unaffected — it signs
> with its own key; see below.)

## 1. Generate a release keystore (one-time)

```bash
keytool -genkeypair -v \
  -keystore gatepath-release.keystore \
  -alias gatepath \
  -keyalg RSA -keysize 4096 -validity 10000
# You'll be prompted for a store password, a key password, and a distinguished
# name. Record the alias ("gatepath") and both passwords.
```

Store `gatepath-release.keystore` offline/securely. Do **not** commit it.

## 2. Configure the GitHub secrets (one-time)

`build.gradle.kts` reads the keystore from four env vars; the workflow supplies
them from these repository secrets:

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | the keystore file, base64-encoded |
| `ANDROID_KEYSTORE_PASSWORD` | the store password |
| `ANDROID_KEY_ALIAS` | the key alias (`gatepath`) |
| `ANDROID_KEY_PASSWORD` | the key password |

```bash
# GNU coreutils (Linux):
base64 -w0 gatepath-release.keystore | gh secret set ANDROID_KEYSTORE_BASE64
# macOS / BSD (no -w flag):
#   base64 gatepath-release.keystore | tr -d '\n' | gh secret set ANDROID_KEYSTORE_BASE64
gh secret set ANDROID_KEYSTORE_PASSWORD   # paste when prompted
gh secret set ANDROID_KEY_ALIAS --body gatepath
gh secret set ANDROID_KEY_PASSWORD        # paste when prompted
```

Without `ANDROID_KEYSTORE_BASE64` the release builds unsigned and says so in the
release notes.

## 3. Cut a release

1. Bump the version in `android/app/build.gradle.kts`:
   - `versionCode` — integer, **must increase** every release.
   - `versionName` — human string, e.g. `1.0.1`.
2. Add a changelog for the new `versionCode` at
   `fastlane/metadata/android/en-US/changelogs/<versionCode>.txt`
   (F-Droid and Play both read these).
3. Commit, then tag and push:
   ```bash
   git tag v1.0.1
   git push origin v1.0.1
   ```
4. `release.yml` builds, signs (if secrets are set), and publishes the GitHub
   Release with `app-release.aab`, `app-release*.apk`, and the SBOM.

## 4. F-Droid

F-Droid is the natural channel for a privacy tool, and it works differently from
Play: **F-Droid builds from source and signs with its own key**, so it does
**not** use your keystore. To publish there:

- The store listing text already lives in-repo at
  `fastlane/metadata/android/en-US/` (title, descriptions, changelogs) — it sits
  at the repo root because that is where F-Droid's fastlane importer scans.
- Submit a build recipe (metadata YAML) to
  [`fdroiddata`](https://gitlab.com/fdroid/fdroiddata) referencing this repo,
  the `v*` tags, and `versionCode`/`versionName`. Keep the build free of
  non-free dependencies (the app is already FOSS with pinned deps).
- Reproducibility helps F-Droid verify builds; the pinned Gradle version catalog
  and lockful CI support this.

## Notes

- **Play upload key vs app signing key.** If you enroll in Play App Signing,
  the keystore above becomes your *upload* key; Google holds the app signing
  key. Either way, keep the upload key safe.
- **Desktop artifacts.** The desktop sysext (`P2.1`) is not attached to these
  releases yet — sysext signing + attaching it is part of the deferred
  "signed releases" follow-up (see ROADMAP P2.3 / DESKTOP_NETNS_DEPLOYMENT.md).
- **Never commit** the keystore, passwords, or the base64 blob.
