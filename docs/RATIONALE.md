# Why Gatepath exists — design rationale

This document explains the problem Gatepath solves, why the obvious
alternatives are worse for a strict always-on-VPN posture, why the design
works, and an honest accounting of what it costs (including the "this adds
attack surface / complexity" criticism, met head-on).

It is the *motivation* companion to [`SECURITY_MODEL.md`](SECURITY_MODEL.md)
(what is and isn't protected) and [`ARCHITECTURE.md`](ARCHITECTURE.md) (how the
pieces fit). Implementation status — including the hwsim validation milestone and the
remaining physical-card confirmation items — lives in [`BLOCKERS.md`](BLOCKERS.md).

---

## 1. The posture Gatepath is built to defend

The target user runs an **always-on, full-tunnel VPN with a kill switch**
(Mullvad lockdown mode, Proton permanent kill switch, a `wg`/`tun` default
route plus a firewall that drops all non-tunnel egress, etc.). Under that
posture:

- Every resident program — sync clients, browsers, package managers,
  telemetry, messaging apps, background daemons — is *designed to run
  constantly* and assumes its traffic is protected.
- The kill switch guarantees that if the tunnel is down, those programs are
  **blocked**, not leaking. That is the whole point: nothing touches the local
  network in the clear, ever.

There is exactly one routine situation that breaks this guarantee: a **captive
portal**.

### The chicken-and-egg

A hotel/airport/café network intercepts all traffic until you authenticate
through its portal page. But:

- Your VPN **cannot establish** — its handshake to the VPN server is itself
  blocked by the portal.
- So at sign-in time you have **no working tunnel**. The kill switch is doing
  its job and blocking everything, which means you also can't reach the portal
  to satisfy it.

You are stuck: the only way onto the internet is to talk *directly* to a
hostile network you don't trust, using the exact interface your entire security
posture exists to keep that network away from.

---

## 2. The obvious fixes all weaken the *whole* posture

The honest baseline is not "do nothing." It's one of these — and each one
relaxes your protection either globally or permanently:

| Approach | What you do | Pros | Cons |
|---|---|---|---|
| **Pause the VPN / disarm the kill switch** | Turn protection off, sign in, turn it back on | Trivial; built into every VPN client | Exposes **everything at once** for the window — every resident app immediately leaks DNS, telemetry, sync, and identifiers onto the hostile network. Relies on a human remembering to re-arm. The blast radius is your entire system. |
| **Split-tunnel / "allow LAN" rule** | Add a firewall/routing exception for local subnets | Targeted; portal reachable while tunnel logic stays mostly intact | A **permanent** hole that usually outlives the one network you opened it for. On a captive network the "LAN" *is* the adversary, so you've whitelisted exactly the party you don't trust — every time, everywhere, forever. |
| **Separate device / Qubes / Tails** | Do the portal on something disposable | Strong isolation; proven model | Not portable to a normal desktop; heavyweight; you carry a second device or run a whole different OS. This is the model Gatepath ports *down* to a single ordinary machine. |

The thing all three share: to handle a 5-minute sign-in, you **degrade the
protection of programs that have nothing to do with the portal** — either
broadly (pause) or durably (split-tunnel). For a posture whose premise is
"*nothing* leaks, *ever*," that is the wrong trade.

---

## 3. Gatepath's approach: one purpose, one compartment, then gone

Gatepath's design premise is a single sentence:

> **Don't weaken the global posture. Build a temporary, kernel-isolated
> compartment, give *only* the portal browser direct access to the hostile
> Wi-Fi, leave everything else firmly behind the VPN/kill-switch, and destroy
> the compartment the moment sign-in is done.**

Concretely, the privileged helper:

1. Creates a dedicated network namespace (`gatepath`).
2. Moves **the captive Wi-Fi interface** into it — and nothing else.
3. Spawns **one** process there: the portal WebView, dropped to your UID.
4. You sign in (or a 10-minute timeout fires).
5. Tears the namespace down; the Wi-Fi interface returns to the host.
6. The VPN now establishes over Wi-Fi, and your normal stack is protected.

Gatepath is **not** another resident daemon competing with your always-on
stack. The helper is D-Bus-activated on demand, the compartment exists only
during sign-in, and the whole thing is idle (or absent) the rest of the time.
Its authority is deliberately tiny: move *the* captive interface, tear it down —
it cannot touch the VPN, the routing table, other interfaces, or other apps.

---

## 4. Why it works (the mechanism, in detail)

### Network namespaces are a kernel partition, not a policy

A Linux network namespace is a complete, independent copy of the kernel's
network stack: its own set of interfaces, its own routing table, its own
firewall, its own sockets. **A process can only ever use the interfaces present
in its own namespace.** This is enforced in the kernel's packet path, not by a
routing rule or a firewall policy that could be misconfigured or bypassed.

So when the captive Wi-Fi interface is the *only* interface in the `gatepath`
namespace and the portal browser is the *only* process there:

- **The browser cannot reach the VPN or your protected stack.** The `tun`/`wg`
  device and everything else live in the host namespace, which the browser has
  no handle on. It physically has no route inward. A compromised portal page
  cannot pivot from the browser into your real traffic, because there is no
  path to pivot along.
- **Your other programs cannot leak onto the captive network.** With the Wi-Fi
  interface removed from the host namespace, host programs have no
  hostile-network interface to use at all — and the kill switch is still armed
  on top of that. Their only egress remains the (not-yet-up) tunnel, so they
  stay blocked exactly as your posture intends.

The isolation is therefore **bidirectional and kernel-enforced**: the portal
can't reach in, and your stack can't leak out, for the duration of the window.

### The key reason the trade is clean: at sign-in you have nothing to lose

The one operation that looks aggressive — *removing the Wi-Fi radio from the
host* — costs you nothing in the captive case, because of the chicken-and-egg in
§1: **at sign-in time there is no working tunnel and no working internet
anyway.** Gatepath borrows the radio while it's useless to you, gets you
authenticated, and hands it straight back. You go from "no connectivity" to
"authenticated, VPN coming up" without your protected programs ever being
exposed in between. Nothing that was working stops working.

This is also why Gatepath keeps your **kill switch armed the whole time**. The
naive fix disarms it; Gatepath never does. Your resident programs remain blocked
from the hostile network throughout — they don't get a chance to leak, because
the protection is never lowered for them. Only the single browser, in its own
compartment, is allowed to talk to the portal.

### Relationship to the Android model (and an honest design tradeoff)

Android achieves the same goal with `bindProcessToNetwork()`, which *binds one
process's sockets* to the captive network while the interface stays shared — a
**non-exclusive** mechanism. Linux has no clean per-process equivalent that
works without privilege (the nearest, `SO_BINDTODEVICE`, needs `CAP_NET_RAW`
and can't be imposed on WebKit's many sockets), so Gatepath uses the namespace
primitive instead, which is **exclusive**: while the compartment holds the
Wi-Fi interface, the host doesn't have it.

That exclusivity is a feature for the captive case (the browser can *only* use
Wi-Fi; nothing else can touch it — simpler and stronger than socket binding),
and a limitation outside it: if a full tunnel were genuinely live and *riding on
that same Wi-Fi*, suspending the radio would drop it for the window. Gatepath is
built for the **initial captive sign-in**, where there is no live tunnel to
disturb — not for rewriting routing under an active session. This is stated
plainly rather than hidden.

### What it does *not* do

Network isolation confines *where packets can go*. It does **not** harden the
browser engine against the hostile page it is rendering. A WebKit
remote-code-execution bug is exactly as exploitable inside the compartment as in
any browser — and the compartment, by design, has a live link to the hostile
network. Gatepath's claim is precise: it removes **network leakage of your
protected stack** during sign-in, kernel-enforced. It does not claim to make
rendering a hostile page safe. (See [`SECURITY_MODEL.md`](SECURITY_MODEL.md) for
the full in/out-of-scope table.)

> This shared-kernel limitation is the main reason a **VM-passthrough backend**
> is worth considering: a guest with its own kernel *does* contain a browser
> RCE. The netns-vs-container-vs-VM tradeoff, and a read-only hardware checker,
> are recorded in [`ISOLATION_BACKENDS.md`](ISOLATION_BACKENDS.md).

---

## 5. Meeting the "added complexity / attack surface" criticism head-on

The strongest objection to Gatepath is fair and worth stating in full: *it adds
a privileged root helper (`CAP_NET_ADMIN`, moves interfaces, spawns processes)
and automatically renders attacker-controlled content — that is more attack
surface than simply pausing the VPN.* Four responses, none of which is "the
criticism is wrong":

1. **Scoped/ephemeral is not the same as resident.** The meaningful
   attack-surface metric is not lines of code; it is *exposure-time × privilege
   × reachability*. A daemon that runs constantly is exposed constantly.
   Gatepath's helper is D-Bus-activated on demand and the compartment lives only
   during sign-in — the exposure-time factor is near-zero outside a brief,
   user-initiated window. Complexity that is **off by default and torn down**
   is categorically cheaper than complexity that **runs all the time** behind
   your always-on VPN.

2. **It replaces a worse alternative, not nothing.** The realistic baseline is
   "pause the VPN" or "split-tunnel allow-LAN" (§2) — both are *also* risk, just
   relocated. Pausing exposes **everything, globally**, for the window;
   allow-LAN is a **permanent** hole that trusts the LAN on a network whose LAN
   is the adversary. Gatepath swaps a *global or permanent* weakening for a
   *narrow and temporary* one. For a strict always-on posture that is a
   favorable trade, not an added one.

3. **The blast radius is bounded by construction.** The helper's authority is
   intentionally minimal: move the one captive Wi-Fi interface, tear it down.
   It has no authorization to touch the VPN, the host routing table, other
   interfaces, `/etc/resolv.conf`, or other apps' traffic. The worst case from a
   fully compromised helper is **your captive sign-in stops working** — not
   compromise of your protected stack. That bound is a design goal, validated by
   the validation/polkit/throttle/fail-closed layers in the helper.

4. **The complexity is the price of *not* weakening the global posture.** This
   is the crux of the whole project. You can have "handle captive portals" *or*
   "never lower protection for unrelated programs" cheaply — but not both. The
   manual fixes are simple precisely because they give up the second property.
   Gatepath spends complexity to keep it. Whether that's worth it depends
   entirely on how strict your posture is; for "nothing leaks, ever," it is.

The right way to keep this honest over time: **the helper must stay smaller and
more auditable than the risk it removes.** If it ever grows into a general
network daemon, the argument above stops holding. Keeping its authority narrow
is not a nicety — it is what makes the trade defensible.

---

## 6. Honest cons / when you do *not* need this

- **It does not harden the browser.** Rendering a hostile portal in WebKit is a
  real code-execution surface that isolation does not address (§4).
- **It is a privileged helper.** Bounded (§5.3), but a root daemon parsing
  D-Bus input is non-zero added surface that did not exist in the manual flow.
- **The radio is exclusive during the window.** Fine for initial sign-in (no
  live tunnel to disturb); disruptive for a mid-session captive re-auth while a
  tunnel is actively carrying traffic.
- **Off-link portals are imperfect.** A portal that lives beyond the local
  gateway is the awkward edge case for any interface-scoped approach.
- **If you don't run an always-on, kill-switched VPN, you probably don't need
  Gatepath.** The OS's built-in captive browser is fine when you have no global
  posture to protect. Gatepath earns its complexity *only* under the strict
  posture in §1.

---

## 7. Status

The reasoning in §4 is a statement about the **design**. On the desktop, the
netns path is now implemented and **validated end-to-end on a `mac80211_hwsim`
virtual radio** (the real kernel Wi-Fi stack) — the wireless PHY move
(`iw phy … set netns`) and in-netns association/DHCP re-establishment
(`wpa_supplicant` + DHCP) are proven by the `tests/e2e-hwsim/` harness, which
also asserts the no-leak invariant. Covers **open** captive networks only.
Physical-card confirmation (real Wi-Fi firmware/RF quirks) is pending but is no
longer the core unproven risk — see [`BLOCKERS.md`](BLOCKERS.md). On Android,
the equivalent guarantee (`bindProcessToNetwork`) is implemented and shipping.
