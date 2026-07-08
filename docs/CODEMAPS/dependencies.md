<!-- Generated: 2026-07-05 | Files scanned: pyproject.toml, Cargo.toml, build.gradle.kts, libs.versions.toml | Token estimate: ~400 -->

# Dependencies Codemap

## Desktop Python (`desktop/pyproject.toml`)
- Core runtime: **zero third-party deps** (stdlib only)
- `gui` extra: `PyGObject`, `dasbus>=1.7` (D-Bus client to the Rust helper)
- `dev` extra: `pytest>=8.0`, `pytest-timeout`
- Packaged as a Flatpak (GNOME SDK's bundled Python + setuptools; no hatchling
  to avoid an extra Flatpak module)

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

External runtime dependents (not crates, invoked as subprocesses): `iw`,
`wpa_supplicant`, a DHCP client, `systemd-run`, PolicyKit/`polkit`,
NetworkManager (via D-Bus).

## Android (`android/app/build.gradle.kts` + version catalog)
- AndroidX: core-ktx, lifecycle (runtime/viewmodel-compose/process), activity-compose
- Compose BOM + ui/graphics/tooling-preview/material3
- Hilt (`hilt.android` + `ksp` compiler, `hilt.navigation.compose`) — DI
- `kotlinx.serialization.json`, `kotlinx.coroutines.android`
- Build: AGP 9 (Kotlin built-in compiler plugin), `compileSdk 37` (Compose BOM
  compatibility bump, 2026-07-04 — see memory `dependabot-triage-and-agp9-migration`)
- Dependabot-managed groups: `cargo` (desktop helper), `github-actions`, `gradle` (android) —
  see `.github/dependabot.yml`

## External services / infra
- **NetworkManager** (D-Bus) — desktop captive-portal device state
- **PolicyKit** — authorizes every privileged D-Bus call to the Rust helper
- **systemd** — sysext packaging (P2.1) + `systemd-run` transient units for portal spawn
- **GitHub Actions self-hosted runner** (`gatepath-hwsim`) — runs the
  mac80211_hwsim real-radio E2E suite that can't run on hosted runners
- **F-Droid / Flathub** — distribution targets, metadata under `distribution/`
