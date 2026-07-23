#![no_main]
//! Fuzz `validate_interface_name` — the VPN/tunnel-leak trust boundary.
//!
//! Two properties, over coverage-guided arbitrary input:
//!   1. It never panics (libFuzzer catches any panic/abort as a crash).
//!   2. Anything ACCEPTED is provably safe.
//!
//! For (2) we assert a *drift-free* proxy for the full invariant: an accepted
//! name is non-empty, within IFNAMSIZ, charset-clean, and starts with `wl`.
//! All three accepted WiFi prefixes (`wlan`/`wlp`/`wlx`) begin with `wl`, and
//! *no* forbidden prefix (`tun`/`tap`/`wg`/`tailscale`/`eth`/`en`/`br`/… ) does
//! — and the validator checks forbidden prefixes first — so "accepted ⇒ starts
//! with `wl`" captures both the WiFi-only rule and the no-VPN-leak rule without
//! duplicating the private allowlists here. The exhaustive per-prefix invariant
//! lives in the in-crate proptest (`validation::proptests`); this target adds
//! coverage-guided panic discovery and a stable acceptance sanity oracle.

use libfuzzer_sys::fuzz_target;

use gatepath_netns_helper::validation::validate_interface_name;

/// Linux IFNAMSIZ is 16 bytes including NUL → 15 usable characters (mirrors the
/// validator's private `MAX_IFNAME_LEN`; a stable kernel constant).
const MAX_IFNAME_LEN: usize = 15;

fuzz_target!(|data: &[u8]| {
    // D-Bus strings are UTF-8; lossy-decode arbitrary bytes to feed the widest
    // realistic domain (matches the proptest's `arb_string`).
    let name = String::from_utf8_lossy(data);

    if validate_interface_name(&name).is_ok() {
        assert!(!name.is_empty(), "accepted an empty interface name");
        assert!(
            name.len() <= MAX_IFNAME_LEN,
            "accepted an over-IFNAMSIZ name: {name:?}"
        );
        assert!(
            name.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-'),
            "accepted a name with a disallowed byte: {name:?}"
        );
        assert!(
            name.starts_with("wl"),
            "accepted a non-WiFi / potentially forbidden interface: {name:?}"
        );
    }
});
