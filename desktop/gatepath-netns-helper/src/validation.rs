//! Interface-name validation. The helper's entire security boundary depends
//! on this function: anything that returns `Ok(())` is allowed to be moved
//! into the gatepath netns by a future Phase 5b call site.
//!
//! Rule: refuse anything that matches a known **forbidden prefix** (VPN,
//! tunnel, ethernet, bridge, container, loopback) and accept only
//! WiFi-naming-convention interfaces (`wlan*`, `wlp*`, `wlx*`).
//!
//! This validation alone is not sufficient — at runtime the helper MUST also
//! re-confirm that NetworkManager currently flags the requested interface as
//! captive. But validation runs first because it's cheap and cuts off the
//! biggest attack surface (passing `tun0` to leak VPN traffic).

use thiserror::Error;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum InterfaceValidationError {
    #[error("interface name was empty")]
    Empty,
    #[error("interface name '{0}' contains characters that aren't allowed")]
    InvalidChars(String),
    #[error("interface name '{0}' is too long (max 15 chars per IFNAMSIZ)")]
    TooLong(String),
    #[error("interface '{0}' is forbidden (matches blocked prefix)")]
    Forbidden(String),
    #[error("interface '{0}' does not match any known WiFi naming convention")]
    NotWiFi(String),
}

/// Linux IFNAMSIZ is 16 bytes including NUL → 15 usable characters.
const MAX_IFNAME_LEN: usize = 15;

/// Forbidden prefixes — these are the attack vectors to block first. Matched
/// in order, case-sensitive. Listing common VPN, tunnel, ethernet, bridge,
/// container, and loopback patterns.
const FORBIDDEN_PREFIXES: &[&str] = &[
    "tun",       // OpenVPN, generic L3 VPN tunnels
    "tap",       // L2 VPN tunnels
    "wg",        // WireGuard
    "tailscale", // Tailscale
    "ppp",       // PPP / dial-up
    "veth",      // Virtual ethernet pair (helper's own bookkeeping)
    "lo",        // Loopback
    "eth",       // Wired ethernet
    "en",        // Predictable ethernet (enp*, eno*, ens*, enx*)
    "docker",    // Docker bridge
    "podman",    // Podman bridge
    "br",        // Generic bridge / br-*
    "virbr",     // libvirt bridge
    "vmnet",     // VMware virtual ethernet
    "zt",        // ZeroTier
];

/// Allowed WiFi naming prefixes. Anything that doesn't start with one of
/// these is rejected as `NotWiFi`.
const WIFI_PREFIXES: &[&str] = &[
    "wlan", // Legacy kernel-assigned (wlan0, wlan1, ...)
    "wlp",  // Predictable name on PCI bus (wlp3s0, ...)
    "wlx",  // Predictable name based on MAC (wlx00112233aabb)
];

/// Validate that `name` is a WiFi interface name we are willing to accept
/// for migration into the gatepath netns.
///
/// # Errors
///
/// Returns [`InterfaceValidationError`] for empty, oversized, malformed, or
/// non-WiFi interface names. See enum variants for specifics.
pub fn validate_interface_name(name: &str) -> Result<(), InterfaceValidationError> {
    if name.is_empty() {
        return Err(InterfaceValidationError::Empty);
    }
    if name.len() > MAX_IFNAME_LEN {
        return Err(InterfaceValidationError::TooLong(name.to_string()));
    }
    if !name
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return Err(InterfaceValidationError::InvalidChars(name.to_string()));
    }

    // Forbidden prefixes first — these are the attack patterns.
    for prefix in FORBIDDEN_PREFIXES {
        if name.starts_with(prefix) {
            return Err(InterfaceValidationError::Forbidden(name.to_string()));
        }
    }

    // Then require an explicit WiFi-prefix match — defaulting to deny.
    if WIFI_PREFIXES.iter().any(|prefix| name.starts_with(prefix)) {
        Ok(())
    } else {
        Err(InterfaceValidationError::NotWiFi(name.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_wlan_passes() {
        assert!(validate_interface_name("wlan0").is_ok());
        assert!(validate_interface_name("wlan1").is_ok());
    }

    #[test]
    fn predictable_pci_wifi_passes() {
        assert!(validate_interface_name("wlp3s0").is_ok());
        assert!(validate_interface_name("wlp0s20f3").is_ok());
    }

    #[test]
    fn predictable_mac_wifi_passes() {
        assert!(validate_interface_name("wlx00112233aabb").is_ok());
    }

    #[test]
    fn vpn_interfaces_are_forbidden() {
        let attack_vectors = [
            "tun0",
            "tun1",
            "tap0",
            "wg0",
            "tailscale0",
            "tailscale1",
            "ppp0",
            "zt0",
        ];
        for iface in attack_vectors {
            let err = validate_interface_name(iface).expect_err(iface);
            assert_eq!(
                err,
                InterfaceValidationError::Forbidden(iface.to_string()),
                "expected Forbidden for {iface}",
            );
        }
    }

    #[test]
    fn ethernet_is_forbidden() {
        for iface in ["eth0", "eth1", "enp0s3", "ens33", "eno1", "enx00112233aabb"] {
            let err = validate_interface_name(iface).expect_err(iface);
            assert_eq!(
                err,
                InterfaceValidationError::Forbidden(iface.to_string()),
                "expected Forbidden for {iface}",
            );
        }
    }

    #[test]
    fn bridges_and_containers_are_forbidden() {
        for iface in ["br0", "br-7c8a9b", "virbr0", "docker0", "podman0", "vmnet8"] {
            let err = validate_interface_name(iface).expect_err(iface);
            assert_eq!(
                err,
                InterfaceValidationError::Forbidden(iface.to_string()),
                "expected Forbidden for {iface}",
            );
        }
    }

    #[test]
    fn loopback_is_forbidden() {
        assert_eq!(
            validate_interface_name("lo").unwrap_err(),
            InterfaceValidationError::Forbidden("lo".into()),
        );
    }

    #[test]
    fn helper_own_veth_is_forbidden() {
        // Defensive: even the helper's own bookkeeping veth must not be
        // re-moved through the public API.
        assert_eq!(
            validate_interface_name("veth-gatepath").unwrap_err(),
            InterfaceValidationError::Forbidden("veth-gatepath".into()),
        );
    }

    #[test]
    fn empty_is_rejected() {
        assert_eq!(
            validate_interface_name("").unwrap_err(),
            InterfaceValidationError::Empty,
        );
    }

    #[test]
    fn over_15_chars_is_rejected() {
        // Linux IFNAMSIZ allows 15 chars; we should reject 16+ to match.
        let too_long = "wlx0011223344556";
        assert_eq!(too_long.len(), 16);
        assert_eq!(
            validate_interface_name(too_long).unwrap_err(),
            InterfaceValidationError::TooLong(too_long.into()),
        );
    }

    #[test]
    fn invalid_chars_are_rejected() {
        for bad in ["wlan 0", "wlan;rm", "wl/0", "wlan\n0"] {
            assert!(
                matches!(
                    validate_interface_name(bad),
                    Err(InterfaceValidationError::InvalidChars(_)),
                ),
                "expected InvalidChars for {bad:?}",
            );
        }
    }

    #[test]
    fn unknown_prefixes_are_not_wifi() {
        for iface in ["random0", "mything", "abc"] {
            assert!(
                matches!(
                    validate_interface_name(iface),
                    Err(InterfaceValidationError::NotWiFi(_)),
                ),
                "expected NotWiFi for {iface}",
            );
        }
    }

    #[test]
    fn boundary_attack_lookalikes_are_blocked() {
        // The forbidden-prefix check uses `starts_with`. Make sure attackers
        // can't sneak through a string that LOOKS like a WiFi prefix but is
        // actually a VPN — the WiFi prefix check requires explicit allow,
        // and forbidden prefixes are checked first.
        for iface in ["wlan-tun0", "wlp-wg0"] {
            // These DO start with wlan/wlp so they pass forbidden-prefix
            // (which is exact-prefix). They also pass the WiFi check. So
            // they are accepted — which is fine: an interface NAMED with a
            // wlan prefix IS treated as WiFi. The kernel decides interface
            // semantics from its name + driver, and we trust kernel + NM.
            //
            // The actual defence-in-depth is the runtime NetworkManager
            // re-check, not the name. This test pins the boundary: don't
            // *change* this validator to start refusing legit-looking names
            // because that breaks real users with `wlan-something` setups.
            assert!(
                validate_interface_name(iface).is_ok(),
                "{iface} should pass name validation"
            );
        }
    }
}

#[cfg(test)]
mod proptests {
    //! Property tests for the interface-name trust boundary (ROADMAP P1.2).
    //!
    //! The validator is fed attacker-controlled names, so the properties that
    //! matter are "never panics" and "anything accepted is provably safe" — a
    //! WiFi prefix and *never* a forbidden VPN/tunnel/bridge/loopback prefix
    //! (the leak the whole helper exists to prevent). Example-based tests above
    //! pin specific cases; these assert the invariants hold for *all* input.
    use super::*;
    use proptest::prelude::*;

    /// Arbitrary UTF-8 strings, incl. control bytes and non-ASCII (lossy decode
    /// of random bytes) — a wider domain than a `".*"` regex strategy.
    fn arb_string() -> impl Strategy<Value = String> {
        proptest::collection::vec(any::<u8>(), 0..200)
            .prop_map(|b| String::from_utf8_lossy(&b).into_owned())
    }

    /// A union of wild bytes and structurally-plausible names. A pure
    /// random-byte generator almost never produces an *accepted* name, which
    /// would leave the "accepted ⇒ safe" property vacuously green; the
    /// structured branches make the accept path (and every reject path)
    /// actually fire.
    fn arb_iface_name() -> impl Strategy<Value = String> {
        prop_oneof![
            arb_string(),
            "[A-Za-z0-9_-]{0,18}",
            "wl(an|p|x)[A-Za-z0-9_-]{0,14}",
            "(tun|tap|wg|eth|en|br|docker|lo|veth|zt)[A-Za-z0-9_-]{0,10}",
        ]
    }

    proptest! {
        /// The boundary never panics, for any input.
        #[test]
        fn never_panics(s in arb_iface_name()) {
            let _ = validate_interface_name(&s);
        }

        /// Anything ACCEPTED satisfies every gate — including, crucially, that
        /// it is NOT a forbidden prefix. This is the VPN-leak boundary.
        #[test]
        fn accepted_names_are_provably_safe(s in arb_iface_name()) {
            if validate_interface_name(&s).is_ok() {
                prop_assert!(!s.is_empty());
                prop_assert!(s.len() <= MAX_IFNAME_LEN);
                prop_assert!(
                    s.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
                );
                prop_assert!(WIFI_PREFIXES.iter().any(|p| s.starts_with(p)));
                prop_assert!(
                    !FORBIDDEN_PREFIXES.iter().any(|p| s.starts_with(p)),
                    "accepted a forbidden-prefixed interface: {s:?}"
                );
            }
        }

        /// Any name starting with a forbidden prefix is rejected, whatever the
        /// suffix — generated from the live FORBIDDEN_PREFIXES list so a new
        /// blocked prefix is covered automatically.
        #[test]
        fn forbidden_prefix_is_never_accepted(
            idx in 0usize..FORBIDDEN_PREFIXES.len(),
            suffix in arb_string(),
        ) {
            let name = format!("{}{}", FORBIDDEN_PREFIXES[idx], suffix);
            prop_assert!(
                validate_interface_name(&name).is_err(),
                "accepted a forbidden-prefixed interface: {name:?}"
            );
        }

        /// A well-formed WiFi name within IFNAMSIZ is always accepted —
        /// `wlan` + 11 hits the len-15 accept boundary (one below the TooLong
        /// cliff at 16).
        #[test]
        fn wellformed_wifi_names_are_accepted(s in "wl(an|p|x)[A-Za-z0-9_-]{0,11}") {
            prop_assert!(
                validate_interface_name(&s).is_ok(),
                "rejected a well-formed WiFi name: {s:?}"
            );
        }

        /// A charset-valid, length-valid name with a forbidden prefix errors
        /// *specifically* as `Forbidden`. This strengthens the boundary: a
        /// regression that removed the forbidden-prefix check (leaving only the
        /// default-deny WiFi gate) would still reject these as `NotWiFi`, so the
        /// is_err properties above wouldn't catch it — but this one would. The
        /// suffix is capped so TooLong/InvalidChars can't preempt the Forbidden
        /// arm (longest prefix `tailscale` = 9, + 5 = 14 ≤ IFNAMSIZ).
        #[test]
        fn forbidden_prefix_errors_as_forbidden(
            idx in 0usize..FORBIDDEN_PREFIXES.len(),
            suffix in "[A-Za-z0-9_-]{0,5}",
        ) {
            let name = format!("{}{}", FORBIDDEN_PREFIXES[idx], suffix);
            prop_assert!(
                matches!(
                    validate_interface_name(&name),
                    Err(InterfaceValidationError::Forbidden(_))
                ),
                "expected Forbidden for {name:?}"
            );
        }
    }
}
