# Gatepath end-to-end harness (Android emulator)

A Docker-based AOSP Android emulator that exercises the Gatepath captive-portal
flow end-to-end — captive detection, the `PortalScreen` WebView rendering the
portal, off-domain blocking, and post-login network validation. Dispatch to the
portal uses the `BuildConfig.DEBUG` debug intent, not the system notification
(that's untappable on a headless emulator — see `HARNESS_NOTES.md`). Sibling to
`tests/e2e-docker/` (which covers the desktop GTK path).

```
            ┌───────────────────────┐        ┌────────────────────────────┐
            │  mockportal-host      │        │  emulator                  │
            │  (python:3-slim)      │        │  (budtmo/docker-android)   │
            │                       │        │                            │
   :18080   │  mockportal/server.py │  ◀──▶  │  Android 14 (API 34)       │
            │  /generate_204,       │  10.0  │  Gatepath APK installed    │
            │  /portal, /login,     │  .2.2  │  captive_portal_*_url      │
            │  /reset, /log         │        │  overridden via settings   │
            │                       │        │  ADB :5555                 │
            └───────────────────────┘        └────────────────────────────┘
```

## What this does and doesn't cover

**Covered:**
- `Settings.Global.captive_portal_*_url` override + Wi-Fi cycle → real
  AOSP `NetworkMonitor` probe → captive detection.
- Debug-intent dispatch (`am ... --es gatepath.debug.portal_url`) →
  `MainActivity` → `PortalScreen` → `GatepathWebView` rendering the mockportal
  `/portal` page. (Tapping the system `CAPTIVE_PORTAL` notification is
  unworkable on a headless emulator — see `HARNESS_NOTES.md`.)
- Off-domain blocking — the portal HTML embeds
  `<script src=https://evil-tracker.example.com/track.js>` and a link to
  `https://external-site.example.com`; mockportal logs every Host header
  it sees. Assertions FAIL HARD if either appears.
- The `submit_login → 302 → /generate_204 → 204` post-login validation
  cycle, ending with the WIFI network reaching `IS_VALIDATED`.

**Not covered:**
- **The real system `CAPTIVE_PORTAL` intent dispatch** to `CaptivePortalActivity`
  with the parcelable `EXTRA_CAPTIVE_PORTAL` token. The grouped system
  notification can't be tapped on a headless emulator (brute-forcing it ANRs
  SystemUI), so the harness dispatches via the debug intent into `PortalScreen`.
  Everything downstream of "PortalScreen showing the portal" is covered; the
  system→activity plumbing is not. See `HARNESS_NOTES.md`.
- **GrapheneOS or other privacy-fork images.** The budtmo image is AOSP,
  not Graphene — Graphene hardcodes captive-probe URLs (see
  `docs/TESTING_ANDROID.md` for the workaround on Graphene devices).
- **Cross-API-level matrix.** API 34 only; budtmo free tier ceiling.
- **WebView form fidelity in `--mode=ui`.** `uiautomator dump` cannot
  reliably reach WebView form inputs at API 34. The default
  `--mode=host-post` skips this by posting `/login` from the host
  orchestrator. The path being tested is "WebView loaded the portal AND
  the network ultimately validated"; the actual form submit is a
  trivial `<form action="/login" method="POST">` and doesn't need to
  ride the WebView for the security claims to hold.
- **Real Wi-Fi flakiness.** The emulator presents a clean NAT; no lossy
  DHCP, no captive lease churn, no re-association mid-portal. Same
  scope choice as `tests/e2e-docker/`.
- **TLS-intercepting captives.** Gatepath probes HTTP-only by design.

## Quick start

```sh
# Build the debug APK first.
(cd ../../android && ANDROID_HOME="$ANDROID_HOME" ./gradlew :app:assembleDebug)

# Run the harness.
cd tests/e2e-android
./run-e2e.sh
```

Requires `/dev/kvm` on the host. If your machine doesn't have KVM, use
the CI workflow path (`.github/workflows/android-e2e.yml`) instead.

The script:
1. Cleans `./artifacts/`.
2. `docker compose build` (mockportal-host image).
3. `docker compose up -d` (mockportal-host + budtmo emulator).
4. Waits for `sys.boot_completed=1`, runs the scenario, exits with its rc.
5. Pulls logcat + audit log + gateway request log into `./artifacts/`.
6. Runs `driver/assertions.py` over the artifacts.

Exit 0 only if the scenario AND every host-side assertion pass.

## Artifacts

After a run, `./artifacts/` contains:

- `scenario-report.json` — every step's outcome from `run-scenario.py`.
- `audit_log.jsonl` — Gatepath app's audit log, pulled via `adb run-as`.
- `gateway-log.json` — every request mockportal received.
- `logcat.txt` — last 2000 lines of logcat.

## Interactive debugging

```sh
# Bring up the stack without running the scenario:
docker compose up -d
# noVNC at http://localhost:6080 — watch the emulator's screen.
# ADB:
adb connect localhost:5555
adb shell ...
# Tear down:
docker compose down --volumes
```

## File map

```
tests/e2e-android/
├── compose.yml                       # podman-compose / docker compose stack
├── run-e2e.sh                        # host-side orchestrator
├── README.md
├── .gitignore
├── mockportal-host/
│   ├── Dockerfile                    # python:3-slim + mockportal/
│   └── entrypoint.sh                 # build_server(host=0.0.0.0, complete_after=1000)
├── scenario/
│   ├── adb_helper.py                 # adb subprocess wrappers (stdlib)
│   ├── run-scenario.py               # the 16-step scenario (debug-intent dispatch)
│   └── ci-script.sh                  # one-line wrapper for emulator-runner action
├── driver/
│   └── assertions.py                 # 3-bucket host-side validator
└── artifacts/                        # gitignored; populated each run
    └── .gitkeep
```

## Tuning knobs

| Var                              | Default                                                    | Where                  |
|----------------------------------|------------------------------------------------------------|------------------------|
| `APK_PATH`                       | `android/app/build/outputs/apk/debug/app-debug.apk`        | orchestrator           |
| `SCENARIO_MODE`                  | `host-post`                                                | orchestrator → scenario|
| `COMPOSE`                        | `docker compose`                                           | orchestrator           |
| `PORTAL_COMPLETE_AFTER`          | `1000`                                                     | mockportal-host        |

## Known friction

- **KVM is a hard requirement** for the budtmo image — there is no
  software-emu fallback in the free tier. CI uses
  `reactivecircus/android-emulator-runner` which handles KVM differently.
- **Emulator boot is the long pole** — expect 60-180s on a cold cache.
  First run also pulls the budtmo image (~1.5 GB).
- **`uiautomator dump` is flaky on transitioning UIs.** The scenario adds
  short sleeps between actions; if a step times out, re-running usually
  recovers.
- **The system captive notification is not driven.** It can't be reliably
  tapped on a headless emulator (auto-grouped/collapsed; brute-forcing it ANRs
  SystemUI), so dispatch goes through the `BuildConfig.DEBUG` debug intent into
  `PortalScreen`. See `HARNESS_NOTES.md` for the full rationale.
- **`--mode=ui` is brittle.** Use `--mode=host-post` (default) for
  deterministic CI runs.
- **budtmo's `emulator_15.0` tag is Pro-only.** API matrix expansion
  beyond 34 requires either the paid Pro tier or a switch to a
  self-hosted AVD pipeline.
