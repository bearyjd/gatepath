# Gatepath end-to-end harness (Docker)

A two-container podman-compose stack that simulates a hotel/airport captive
WiFi well enough to exercise the desktop Gatepath flow end-to-end — including
the privileged Rust netns helper crate.

```
            ┌─────────────────────────┐         ┌──────────────────────────┐
            │   captive-gateway       │         │   gatepath-client        │
            │   (Alpine)              │         │   (Fedora 41)            │
            │                         │         │                          │
   :80      │  nginx default_server   │         │  Xvfb :99                │
   :53/53/67│  dnsmasq wildcard A     │  ◀───▶  │  dbus-daemon (system)    │
            │  mockportal :18080      │ captive │  polkitd                 │
            │                         │  _net   │  python-dbusmock NM      │
            │  → /portal, /login,     │         │  desktop Python pkg      │
            │    /reset, /log         │         │  gatepath-netns-helper   │
            │  → any path = 302 to    │         │   (Rust, system D-Bus)   │
            │    mockportal/204 path  │         │                          │
            └─────────────────────────┘         └──────────────────────────┘
```

## What this does and doesn't cover

**Covered:**
- Captive detection (`portal_probe.probe`) against a real HTTP intercept
- NetworkManager captive lookup (`NMCaptiveInterfaceLookup`) against a
  python-dbusmock NM advertising `wlan0` with `Connectivity=PORTAL`
- D-Bus activation of the Rust helper, PolicyKit auth (test override → YES)
- Real kernel ops: `ip netns add gatepath`, `ip link set wlan0 netns gatepath`
- Helper spawning the WebView runner via `setns + setresuid + execv`
- Helper writing its JSONL audit log
- Off-domain blocking — the portal HTML has `<script src=evil-tracker.example.com>`
  and an external link, and the gateway records every Host header that
  reaches mockportal. The assertion script fails if either off-domain
  hostname appears.

**Not covered (yet):**
- Android — that's an emulator problem, not a Docker one.
- Real captive WiFi flakiness (lossy DHCP, captive lease churn, Wi-Fi
  re-association mid-portal). The static IP path here is the happy path.
- TLS-intercepting captives. Gatepath's probe is HTTP-only by design.

## Quick start

```
cd tests/e2e-docker
./run-e2e.sh
```

The script:
1. cleans `./artifacts/`
2. `podman-compose build`
3. `podman-compose up` — the client runs the scenario, exits with its rc
4. snapshots the gateway's `/log` (request journal) into artifacts
5. tears the stack down
6. runs `driver/assertions.py` to validate everything

Exit code is `0` only if the scenario AND every host-side assertion pass.

## Artifacts

After a run, `./artifacts/` contains:
- `scenario-report.json` — every step's outcome from `run-scenario.py`
- `helper-audit.jsonl` — the Rust helper's audit log
- `gateway-log.json` — every request mockportal received
- `scenario-screenshot.png` — Xvfb screen capture (scrot) of whatever the WebView rendered

## Interactive debugging

```
# Bring up the stack with the client idling instead of running the scenario:
podman-compose run --rm gatepath-client wait

# In another terminal:
podman exec -it gatepath-e2e-client bash
# inside the container — every fixture is up; you can poke at things:
runuser -u tester -- python3 -c "from gatepath.netns_client import NetnsClient; ..."
```

## File map

```
tests/e2e-docker/
├── compose.yml             # podman-compose stack definition
├── run-e2e.sh              # host-side orchestrator
├── README.md
├── gateway/
│   ├── Dockerfile          # Alpine + dnsmasq + nginx + mockportal
│   ├── dnsmasq.conf        # wildcard A → 172.30.0.2 + DHCP range
│   ├── nginx.conf          # listen :80 default_server, intercept-all
│   └── entrypoint.sh
├── client/
│   ├── Dockerfile          # Fedora 41 + GTK4/WebKit + Rust helper build
│   ├── entrypoint.sh       # rename iface, dbus/polkit/dbusmock/Xvfb fixtures
│   ├── dbusmock_nm.py      # python-dbusmock seeding wlan0 + PORTAL
│   ├── dbusmock-nm.conf    # system-bus policy: root can own NM name
│   ├── polkit-test.rules   # auto-YES for both helper actions
│   ├── portal-webview-runner.test  # test wrapper: static IP, then python runner
│   └── run-scenario.py     # the E2E scenario itself
└── driver/
    └── assertions.py       # host-side cross-artefact validation
```

## Tuning knobs

Environment variables read by the components:

| Var                                  | Default                                  | Where     |
|--------------------------------------|------------------------------------------|-----------|
| `PORTAL_COMPLETE_AFTER`              | 1                                        | gateway   |
| `GATEPATH_PROBE_URL`                 | `http://connectivity-check.ubuntu.com/`  | scenario  |
| `GATEPATH_WEBVIEW_DWELL_SECONDS`     | 6                                        | scenario  |

## Known friction

- **Rust build is the long pole** — first run pulls down zbus + nix + the
  full dependency graph and compiles them. Expect 5–15 min on a cold cache.
  The Dockerfile pre-builds an empty `src/main.rs` first so the dep cache
  layer survives source changes.
- **Rootless podman + setns** — works on host kernels ≥ 5.11 (user-namespace
  unshare relaxed). Older kernels may need `--privileged` instead of the
  current `cap_add: [NET_ADMIN, SYS_ADMIN, NET_RAW] + seccomp=unconfined`.
- **No real WiFi** — the moved interface here is a veth pair masquerading as
  `wlan0`. Real production would have a `wlan0` from a hardware driver.
  The helper doesn't care about iface backing as long as `ip link set …
  netns …` succeeds and dbusmock reports it as captive.
