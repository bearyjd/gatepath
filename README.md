# Gatepath

A security-focused captive portal handler for **Android** and **Linux desktop**.

When you connect to a hotel, airport, or cafe WiFi, the network typically intercepts
HTTP traffic until you authenticate through their portal page. Gatepath gives you a
controlled, isolated window for completing that sign-in *without* exposing your VPN
tunnel, encrypted DNS, or normal browsing traffic to the portal operator.

> **Read first:** [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md) — what Gatepath
> does and does not protect, by platform.
>
> **Why this exists:** [`docs/RATIONALE.md`](docs/RATIONALE.md) — the problem, why the
> obvious fixes (pause the VPN, split-tunnel) weaken your whole posture, why the
> single-purpose compartment works, and an honest accounting of its costs.

## Repo layout

```
gatepath/
├── android/      Kotlin / Jetpack Compose / Hilt — APK, F-Droid target
├── desktop/      Python 3.11+ / GTK4 / libadwaita / WebKit2GTK — Flatpak, Flathub target
├── mockportal/   Shared mock captive portal (Python stdlib only) — used by tests
└── docs/         SECURITY_MODEL, AUDIT_LOG_SCHEMA, ARCHITECTURE, BLOCKERS
```

The two apps share the audit-log schema ([`docs/AUDIT_LOG_SCHEMA.md`](docs/AUDIT_LOG_SCHEMA.md))
and the security model. They share **no code** — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Platform comparison

| Capability                                             | Android        | Desktop (Flatpak) |
|--------------------------------------------------------|----------------|-------------------|
| Detect captive portal                                  | NetworkCallback| NetworkManager D-Bus + urllib fallback |
| Bind portal traffic to WiFi interface                  | Yes (kernel)   | **No** in Flatpak; native netns helper validated on a `mac80211_hwsim` virtual-radio harness (`tests/e2e-hwsim/`), open networks only; physical-card confirmation pending — see [`docs/BLOCKERS.md`](docs/BLOCKERS.md) |
| Keep VPN tunnel active during portal session           | Yes            | Best-effort, **user warned** |
| Block off-domain navigation in portal window           | Yes            | Yes |
| Block analytics / tracker resource requests            | Yes            | Yes |
| Wipe cookies / cache / storage on session close        | Yes            | Yes |
| Auto-close session after timeout                       | Yes (10 min)   | Yes (10 min) |
| Append-only audit log                                  | Yes (JSONL)    | Yes (JSONL) |

The desktop app is honest about what it cannot guarantee in a Flatpak sandbox — see
the security model. A privileged netns helper (`desktop/gatepath-netns-helper/`)
closes the WiFi-binding gap for native (non-Flatpak) installs on Linux, including
atomic distros like Bazzite: it moves the whole Wi-Fi PHY into a dedicated netns
(`iw phy … set netns`) and re-establishes connectivity inside it (`wpa_supplicant`
+ DHCP). This is validated end-to-end on a `mac80211_hwsim` virtual-radio harness
(`tests/e2e-hwsim/`) for **open** captive networks; physical-card confirmation is
pending. Its status, the remaining confirmation items, and the deployment options
are documented in
[`docs/DESKTOP_NETNS_DEPLOYMENT.md`](docs/DESKTOP_NETNS_DEPLOYMENT.md) and
[`docs/BLOCKERS.md`](docs/BLOCKERS.md).

## Build

### Android

```bash
cd android
./gradlew :app:assembleDebug         # produces app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:test                  # full unit test suite via Android Gradle plugin
```

For development without an Android SDK install — pure-Kotlin JVM unit tests:

```bash
# Requires JDK 21 + kotlinc 2.0.x + Python 3 (for the mockportal subprocess)
bash android/run-jvm-tests.sh
```

### Desktop

```bash
cd desktop
python -m pytest tests/                # full test suite
python -m pip install -e '.[gui]'      # install with GUI extras (PyGObject, dasbus)
python -m gatepath                     # run

# Flatpak build
flatpak-builder --install --user --force-clean build cc.grepon.Gatepath.yml
flatpak run cc.grepon.Gatepath
```

### Mock portal (used by both test suites)

```bash
python -m pytest mockportal/           # tests for the mock server itself
python -m mockportal.server            # run on 127.0.0.1:18080 by default
```

## Distribution

| App      | Target store    | Notes |
|----------|-----------------|-------|
| Android  | F-Droid         | Reproducible builds, no proprietary deps. Play Store as a stretch goal. |
| Desktop  | Flathub         | `cc.grepon.Gatepath.yml` is the manifest. |

## License

TBD (likely Apache-2.0 or GPL-3.0). Both apps will be open source.

## Status

**MVP — not yet released.** See [`docs/BLOCKERS.md`](docs/BLOCKERS.md) for outstanding
build-environment caveats.
