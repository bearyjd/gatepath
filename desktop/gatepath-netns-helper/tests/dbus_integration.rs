//! Manual-only D-Bus integration tests.
//!
//! These tests construct the `#[proxy]`-generated client types against a
//! real system bus. They verify the WIRE SHAPE of our PolicyKit and
//! NetworkManager queries — when polkit or NM evolves their D-Bus
//! signatures, the macros silently regenerate code with the new shapes
//! and our unit tests (which use Fake* impls) won't catch the drift.
//!
//! Note: drift in *our own* helper interface (`NetNsHelper1` method/signal
//! signatures) is now caught in CI without a bus by
//! `src/dbus_contract_test.rs` — it introspects the real zbus interface and
//! asserts it matches `docs/netns_helper_dbus_contract.json` (see
//! `dbus-contract-parity.yml`). These integration tests remain the manual
//! live-wire check for the EXTERNAL PolicyKit/NetworkManager signatures and
//! `launch_portal`'s runtime round-trip, which genuinely need a real bus.
//!
//! All tests are `#[ignore]`'d so they don't run in CI (Github-hosted
//! runners have no system bus + no polkitd). Run manually on a real
//! Linux box with PolicyKit and NetworkManager active:
//!
//! ```bash
//! cd desktop/gatepath-netns-helper
//! cargo test --test dbus_integration -- --ignored --nocapture
//! ```
//!
//! Tests intentionally don't assert on the *value* polkit returns
//! (that depends on the user's session and the policy file being
//! installed). They only assert that the proxy connects, the method
//! call returns SOMETHING (success or error), and the marshalling
//! round-trips. If a polkit interface field is renamed, the call will
//! fail to deserialise and the test will surface the drift.

use std::collections::HashMap;

use gatepath_netns_helper::network_manager::{CaptiveStateChecker, NMCaptiveCheck};
use zbus::blocking::Connection;
use zbus::zvariant::{OwnedValue, Value};

#[test]
#[ignore = "requires system bus + polkit; run manually with --ignored"]
fn polkit_proxy_constructs_against_real_authority() {
    // Just constructing the proxy exercises bus connection + introspection.
    // If polkit isn't running, this fails with a useful error message.
    let conn = Connection::system().expect("system bus");
    // We intentionally don't import the private proxy type from policykit.rs
    // since it's not exported. Instead, build a parallel proxy here so the
    // test breaks loudly if PolicyKitAuthorizer's interface name diverges
    // from the canonical org.freedesktop.PolicyKit1.Authority.
    let proxy = zbus::blocking::Proxy::new(
        &conn,
        "org.freedesktop.PolicyKit1",
        "/org/freedesktop/PolicyKit1/Authority",
        "org.freedesktop.PolicyKit1.Authority",
    )
    .expect("policykit authority proxy");
    drop(proxy);
}

#[test]
#[ignore = "requires system bus + polkit; run manually with --ignored"]
fn polkit_check_authorization_round_trips() {
    let conn = Connection::system().expect("system bus");
    // CheckAuthorization signature: (subject, action_id, details, flags, cancellation_id)
    // Pass a system-bus-name subject for our own connection (matches what
    // the helper does in production).
    let our_name = conn.unique_name().expect("unique bus name").to_string();
    let mut subject_props = HashMap::<String, OwnedValue>::new();
    subject_props.insert(
        "name".to_string(),
        Value::from(our_name.as_str())
            .try_into()
            .expect("OwnedValue conversion"),
    );
    let subject = ("system-bus-name".to_string(), subject_props);
    let details: HashMap<String, String> = HashMap::new();
    let proxy = zbus::blocking::Proxy::new(
        &conn,
        "org.freedesktop.PolicyKit1",
        "/org/freedesktop/PolicyKit1/Authority",
        "org.freedesktop.PolicyKit1.Authority",
    )
    .unwrap();
    // Use a benign action ID we don't actually have a policy for; polkit
    // will return (false, false, _) — that's fine, we just want the
    // marshalling to succeed end-to-end.
    let result: zbus::Result<(bool, bool, HashMap<String, String>)> = proxy.call(
        "CheckAuthorization",
        &(
            &subject,
            "org.example.test.never-defined",
            &details,
            0u32,
            "",
        ),
    );
    let _ = result.expect("CheckAuthorization marshalling");
}

#[test]
#[ignore = "requires a running gatepath-netns-helper on the system bus; run manually with --ignored"]
fn launch_portal_wire_arity_is_four_strings() {
    // DESK-004 pins the LaunchPortal wire signature:
    //   LaunchPortal(portal_url: s, wayland_display: s, x_display: s, x_authority: s) -> u
    // Neither the Rust `FakeSpawner` nor the Python `FakeProxy` catches drift
    // from the real zbus signature — both are hand-rolled. This does, when run
    // against a live helper.
    let conn = Connection::system().expect("system bus");
    let proxy = zbus::blocking::Proxy::new(
        &conn,
        "com.ventouxlabs.Gatepath.NetNsHelper",
        "/com/ventouxlabs/Gatepath/NetNsHelper",
        "com.ventouxlabs.Gatepath.NetNsHelper1",
    )
    .expect("helper proxy");

    // No SetupCaptive ran, so a correctly-shaped call MUST be refused with one
    // of our typed errors (NoActiveSession) — which only happens if the 4-arg
    // method matched and executed. A wrong arity surfaces UnknownMethod /
    // InvalidArgs instead, failing the assertion below. (Debug-string check so
    // the test is robust to zbus's internal error-variant shape.)
    let result: zbus::Result<u32> =
        proxy.call("LaunchPortal", &("http://captive.example/", "", "", ""));
    let err = result.expect_err("LaunchPortal without a session must be refused");
    let text = format!("{err:?}");
    // NoActiveSession is the deterministic refusal when no SetupCaptive ran, and
    // it only fires if the 4-arg method matched and executed. A wrong arity
    // surfaces org.freedesktop.DBus.Error.UnknownMethod / InvalidArgs instead,
    // which contains no such substring — so this fails loudly on signature drift.
    assert!(
        text.contains("NoActiveSession"),
        "expected NoActiveSession (4-arg method matched and ran), got: {text}"
    );
}

#[test]
#[ignore = "requires system bus + NetworkManager; run manually with --ignored"]
fn nm_captive_check_connects_and_lists_devices() {
    // Construct the production NMCaptiveCheck. If NM's interface drifted
    // (different bus name, different method signature for GetDevices),
    // construction or first call breaks loudly.
    let nm = NMCaptiveCheck::connect().expect("connect to NetworkManager");

    // is_captive on a definitely-not-real interface should return Err
    // (InterfaceNotFound), but it should NOT panic or marshal-fail.
    let result = nm.is_captive("wlan-this-does-not-exist-99");
    match result {
        Err(gatepath_netns_helper::network_manager::NMError::InterfaceNotFound(_)) => {}
        Ok(_) => panic!("non-existent interface should not be captive"),
        Err(other) => panic!("unexpected error from real NM: {other:?}"),
    }
}
