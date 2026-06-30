# `tests/e2e-hwsim` — virtual-radio validation harness (ROADMAP P0.2)

This harness runs the **real** privileged `gatepath-netns-helper` through the
full desktop isolation path — **PHY move → in-netns `wpa_supplicant` → DHCP →
transient-unit runner spawn → teardown** — on two **`mac80211_hwsim`** virtual
radios, with **no real captive Wi-Fi**, and proves the core invariant the
product exists for:

> **No-leak:** a sentinel on the user's *trusted* network is reachable from the
> host but **must not** be reachable from inside the `gatepath` netns.

It is the software path to closing **BLOCKER-DESK-003 / [#45]** without physical
hardware, and it flips the `tests/e2e-docker` confinement gate from
`skipped` → `proven` (a veth has no PHY/radio for the privileged path; a virtual
radio does). See `docs/ROADMAP.md` P0.1 / P0.2 and `.claude/PRPs/handoff.md`.

[#45]: https://github.com/bearyjd/gatepath/issues/45

> **Honest-framing rule:** until this harness runs green on a box, the desktop
> isolation path stays "implemented, pending real-hardware validation, open
> networks only." Update `docs/ROADMAP.md` / `docs/BLOCKERS.md` / `README` only
> **after** a real green run.

---

## Requirements

Run `preflight.sh` first — it reports exactly what's present and proves the
crux PHY move actually works on your box:

```bash
sudo bash tests/e2e-hwsim/preflight.sh    # read-mostly; safe, self-cleaning
```

The three must-haves are `hwsim_module=yes`, `hwsim_load=yes`, `phy_move=yes`.

Beyond those the harness needs (all checked at startup): `iw`,
`wpa_supplicant`, `dnsmasq`, `nmcli` (**NetworkManager active**), `ip`,
`python3`, `busctl`, `curl`, and a Rust toolchain to build the helper
(`build-helper.sh` bootstraps `rustup` into `~/.cargo` if needed).

It is designed for an **immutable, read-only-`/usr`** host (Bazzite /
Silverblue): everything installs under writable `/var/lib`, `/etc`, `/run`, and
`$HOME`. It **cannot** run in a container or the Claude sandbox (no netns /
module privilege).

---

## Running it

```bash
# 1. Build the helper as your NORMAL user (toolchain lives in ~/.cargo):
bash tests/e2e-hwsim/build-helper.sh

# 2. Run the harness as root:
sudo bash tests/e2e-hwsim/run.sh                 # default: --dhcp static, headless
```

Flags:

| Flag | Default | Meaning |
|------|---------|---------|
| `--dhcp static\|real` | `static` | In-netns DHCP. `static` pins the lease and exercises the helper's real DHCP-exec path without an on-wire exchange (same fidelity as the docker e2e). `real` does a genuine DISCOVER/OFFER over the hwsim link (needs `busybox`/`udhcpc`). |
| `--webview` | off | Install the marker so the runner execs the real WebKit WebView. Needs a graphical session + the `gatepath` package importable; **headless is all the no-leak gate needs**. |
| `--keep` | off | Skip teardown so you can inspect radios/netns/logs. Clean up later with `--teardown-only`. |
| `--teardown-only` | — | Run only cleanup for a previous (possibly crashed) run. |
| `--help` | — | Usage. |

**Why static DHCP is the default:** the helper *waits on* the DHCP client and
fails `SetupCaptive` if it returns non-zero, so a flaky real-DHCP provision
would block the very confinement proof we want. Static still runs the helper's
real DHCP-exec code path; only the on-wire exchange is faked.

---

## What it stands up

```
 host netns                                     gatepath netns (throwaway)
 ┌───────────────────────────────────────┐      ┌──────────────────────────────┐
 │ gpap0   192.168.77.1                  │      │ wlangp0  192.168.77.50       │
 │   open AP (wpa_supplicant)            │      │   (helper moved this PHY     │
 │   dnsmasq DHCP/DNS  (→ .1)            │      │    into the netns)           │
 │   mockportal  192.168.77.1            │      │                              │
 │                                       │      │ runner probes from inside:   │
 │ gpsen0  10.123.0.1                    │      │   portal    → MUST reach     │
 │   "trusted-net" sentinel              │      │   sentinel  → MUST NOT reach │
 └───────────────────────────────────────┘      └──────────────────────────────┘
  reachable from the host
                                                netns has only the captive link;
                                                no route to the sentinel (confined)
```

`run.sh` then drives the helper over the **real system bus** with `busctl`:
`SetupCaptive("wlangp0")` → `LaunchPortal(...)` → reads the runner's verdict →
`TeardownCaptive()`, asserting at each step. The runner
(`portal-webview-runner.hwsim`, installed at the helper's compile-time
`PORTAL_RUNNER_PATH`) is what runs *inside* the netns and writes the no-leak
verdict to `/tmp/gatepath-hwsim-runner.json`.

---

## What it proves vs. defers

**Proven on a green run:**
- The real DESK-001 PHY move into a throwaway netns.
- DESK-002 in-netns re-association (`wpa_supplicant`) + DHCP exec.
- DESK-003 C4 transient-unit (`systemd-run`) runner spawn joining the netns.
- **The no-leak invariant**: the netns reaches the captive portal but **cannot**
  reach the trusted-net sentinel.
- Helper teardown returns the system to a clean state.

**Deferred / not covered here:**
- **Secured networks** (WPA2-PSK/EAP) — intentional limit; open networks only.
- **Real WebKit rendering** under `--webview` needs a desktop session and the
  `gatepath` package importable in the transient unit's clean env; the headless
  path (default) proves confinement without it.
- **The caller-UID/desktop wrinkle:** here the D-Bus caller is **root**, so the
  helper runs the runner as root in the netns (fine headless). A real desktop
  launches it as the session user; that path is exercised by the GUI, not here.
- **CI:** not wired in yet — needs a runner that can load `mac80211_hwsim`.

---

## Safety & side effects

The harness is careful (`trap cleanup EXIT`, unconditional, idempotent) but it
**does touch system state** for the duration of a run, all restored on exit:

- Loads `mac80211_hwsim` (only unloaded on exit **if this run loaded it**).
- Creates two virtual radios renamed `gpap0`/`wlangp0` and a dummy `gpsen0`; it
  **never touches a real `phy0`** (it claims only freshly-created hwsim phys).
- Creates the `gatepath` netns (the helper owns this).
- Installs, then removes on teardown:
  - `/etc/dbus-1/system.d/cc.grepon.Gatepath.NetNsHelper.conf` (+ `ReloadConfig`)
  - `/etc/polkit-1/rules.d/49-gatepath-hwsim.rules` (auto-allows the helper
    actions — **test box only**, never ship this)
  - an nftables `forward` drop on `gpap0` (belt for the confinement proof)
- **NetworkManager connectivity check (global, brief):** to make NM flag the
  client `PORTAL` (the helper's `is_captive` gate), it drops in
  `/etc/NetworkManager/conf.d/99-gatepath-hwsim-connectivity.conf` pointing the
  check at `http://192.168.77.1/generate_204`. While present, **other
  interfaces may briefly report captive/limited connectivity.** It is removed
  immediately after `SetupCaptive` and again in cleanup.
- Leaves `/var/lib/gatepath/helper-audit.jsonl` in place for inspection.

If a run crashes, re-run with `sudo bash tests/e2e-hwsim/run.sh --teardown-only`.

It picks unused-looking ranges (`192.168.77.0/24`, `10.123.0.0/24`); if those
collide with something real on your box, edit `lib.sh`.

---

## Troubleshooting

Logs land under `/tmp/gatepath-hwsim.XXXXXX/` (kept with `--keep`):
`helper.log`, `wpa-ap.log`, `dnsmasq.log`, `mockportal.log`, `sentinel.log`,
`nmcli-connect.log`, and the `*.err` files from each D-Bus call.

- **`SetupCaptive refused: …NotCaptive`** — NM never flagged `wlangp0` as
  `PORTAL`. Check `nmcli -g GENERAL.CONNECTIVITY device show wlangp0`. The client
  must be associated to `GatepathHwsim` and NM's connectivity poll must have run
  (interval 5s). Confirm the portal answers: `curl http://192.168.77.1/generate_204`
  should `302`.
- **Helper never claims the bus name** — see `helper.log`. Common causes: the
  D-Bus `.conf` not reloaded, PolicyKit/NetworkManager not reachable, or the
  runner missing at `PORTAL_RUNNER_PATH` (the helper refuses to start without
  it). The helper and `build-helper.sh` must agree on `RUNNER_INSTALL_PATH`
  (`lib.sh`) — if you change it, rebuild.
- **Client won't associate** — the two hwsim radios must share a channel
  (both on 2412/ch1 here); confirm `iw reg set US` succeeded and check
  `wpa-ap.log`.
- **`LEAK` reported** — the netns reached the sentinel. If your host has
  `ip_forward=1` and no `nft`/`iptables` was available to install the forward
  block, the AP gateway may be routing the netns onward; install `nftables`.
