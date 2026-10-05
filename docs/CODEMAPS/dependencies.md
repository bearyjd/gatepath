<!-- Updated: 2026-10-04 | Sources: Python/Rust manifests, RPM spec, Android version catalog and CI -->

# Dependencies Codemap

## Desktop Python (`desktop/pyproject.toml`)
- Core runtime: **zero third-party deps** (stdlib only)
- `gui` extra: `PyGObject`, `dasbus>=1.7` (D-Bus client to the Rust helper)
- `dev` extra: `pytest>=8.0`, `pytest-timeout`
- CI-only test deps (not in pyproject): `pyyaml`, `python-dbusmock` (private-bus
  NM wire-contract test), `PyGObject`/`dasbus` — installed by `desktop.yml`
- Version: `1.1.0`; native RPM installs the app plus helper/runner. Required
  RPM GUI stack: `python3-gobject` (including Cairo), `python3-dasbus>=1.7`,
  GTK4, libadwaita and WebKitGTK 6.0. Fedora CI imports the installed UI/runner
  after installing with optional dependencies disabled and checks the runner's
  malformed-URL rejection before GTK initialization.
- Flatpak uses GNOME SDK's bundled Python + setuptools.

## Desktop Rust helper (`desktop/gatepath-netns-helper/Cargo.toml`)
| Crate | Purpose |
|-------|---------|
| `zbus` 5 (tokio, blocking-api) | D-Bus server, no extra async-runtime glue crate |
| `tokio` | async runtime |
| `thiserror` / `anyhow` | error types |
| `tracing` / `tracing-subscriber` | structured logging |
| `serde` / `serde_json` | audit-log JSONL entries |
| `chrono` (clock, serde) | ISO 8601 timestamps |
| `url` | RFC 3986 portal-URL validation |
| `libc` | `O_NOFOLLOW` in `connectivity.rs` |

`unsafe_code = "deny"` lint — crate is unsafe-free since DESK-003 C4 (portal
spawn moved to a transient `systemd-run` unit instead of hand-rolled
fork/setns/setresuid; dropped the `nix` dependency).

**Fuzz crate** (`fuzz/Cargo.toml`, its own workspace): `libfuzzer-sys`
0.4 + a path dep on the helper + `url` — nightly-only cargo-fuzz targets for the
five boundary validators. Not built by the parent `cargo` invocations; scheduled
`fuzz.yml` runs nightly soaks outside PR checks.

External runtime dependents (not crates, invoked as subprocesses): `iw`,
`wpa_supplicant`, a DHCP client, `systemd-run`, PolicyKit/`polkit`,
NetworkManager (via D-Bus).

## Android (`android/app/build.gradle.kts` + version catalog)
- AndroidX: core-ktx, lifecycle (runtime/viewmodel-compose/process), activity-compose
- Compose BOM + ui/graphics/tooling-preview/material3
- Hilt (`hilt.android` + `ksp` compiler, `hilt.navigation.compose`) — DI
- `kotlinx.serialization.json`, `kotlinx.coroutines.android`
- Build: AGP `9.4.1` (built-in Kotlin), compiler plugins `2.4.20`, Gradle
  `9.8.0`, JDK 21; `compileSdk 37`, `targetSdk 35`, `minSdk 29`.
  App version `1.1.0`; authoritative pins live in the version catalog/wrapper.
- Dependabot-managed groups: `cargo` (desktop helper), `github-actions`, `gradle` (android) —
  see `.github/dependabot.yml`

## External services / infra
- **NetworkManager** (D-Bus) — desktop captive-portal device state
- **PolicyKit** — authorizes every privileged D-Bus call to the Rust helper
- **systemd** — sysext packaging (P2.1) + `systemd-run` transient units for portal spawn
- **GitHub Actions self-hosted runner** (`gatepath-hwsim`) — runs the
  mac80211_hwsim virtual-radio E2E suite requiring privileged host access
- **F-Droid / Flathub** — distribution targets, metadata under `distribution/`
