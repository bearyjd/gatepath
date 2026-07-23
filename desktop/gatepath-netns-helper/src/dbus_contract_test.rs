//! D-Bus contract drift guard (Rust side).
//!
//! Asserts that the helper's ACTUAL zbus-generated D-Bus interface
//! (`com.ventouxlabs.Gatepath.NetNsHelper1`, defined by the
//! `#[zbus::interface]` impl on [`crate::dbus_service::DbusService`]) matches
//! the shared, checked-in contract at `docs/netns_helper_dbus_contract.json`.
//! The Python client is guarded against the SAME artifact
//! (`desktop/tests/test_dbus_contract.py`), so method arities, return types,
//! signal shapes, and the bus identifiers cannot silently drift between the
//! two languages. This mirrors the `schema-parity.yml` pattern (audit-log
//! schema, desktop <-> Android) and the error-name guard in
//! `desktop/tests/test_netns_client.py`.
//!
//! This test runs under a plain `cargo test` with **no D-Bus/system/session
//! bus**: zbus's `#[interface]` macro generates the introspection XML from
//! static metadata via [`zbus::object_server::Interface::introspect_to_writer`],
//! which needs only an interface instance, not a connection. We construct a
//! real `DbusService` with the crate's backend fakes and introspect it — so
//! we validate the REAL signatures, not a hand-parse of the source.
//!
//! The live-bus wire round-trip (`tests/dbus_integration.rs`) stays
//! `#[ignore]`d; this is the CI-runnable static-drift detector it points at.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use std::time::Duration;

use serde::Deserialize;
use zbus::object_server::Interface;

use crate::audit_log::FakeAuditWriter;
use crate::auth::FakeAuthorizer;
use crate::backstop::FakeBackstop;
use crate::caller_uid::FakeCallerUidLookup;
use crate::connectivity::FakeNetnsConnectivity;
use crate::dbus_service::{BUS_NAME, DbusService, INTERFACE, OBJECT_PATH};
use crate::name_watch::FakeNameWatcher;
use crate::netns::FakeNetnsOps;
use crate::network_manager::FakeCaptiveCheck;
use crate::service::{BackstopConfig, Deps, GatepathHelperService};
use crate::spawn::FakeSpawner;
use crate::throttle::Throttle;

// ── The shared contract artifact ────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct Contract {
    bus_name: String,
    object_path: String,
    interface: String,
    error_prefix: String,
    methods: BTreeMap<String, MethodSpec>,
    signals: BTreeMap<String, SignalSpec>,
}

#[derive(Debug, Deserialize)]
struct MethodSpec {
    #[serde(rename = "in")]
    inputs: Vec<String>,
    out: String,
}

#[derive(Debug, Deserialize)]
struct SignalSpec {
    args: Vec<String>,
}

/// Path is resolved relative to the crate root so the test is CWD-independent.
/// `CARGO_MANIFEST_DIR` is `.../desktop/gatepath-netns-helper`; the repo root
/// is two levels up.
fn load_contract() -> Contract {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../docs/netns_helper_dbus_contract.json"
    );
    let data = std::fs::read_to_string(path).unwrap_or_else(|e| {
        panic!(
            "failed to read the shared D-Bus contract at {path}: {e}\n\
             (this test drift-guards the zbus interface against that file; \
             if it moved, update the path here and test_dbus_contract.py)"
        )
    });
    serde_json::from_str(&data)
        .unwrap_or_else(|e| panic!("failed to parse D-Bus contract at {path}: {e}"))
}

/// The wire error-name prefix from the `HelperError` enum's
/// `#[zbus(prefix = "…")]` attribute in `dbus_service.rs`.
///
/// This one attribute is NOT part of the interface introspection XML (it maps
/// error variants to `<prefix>.<Variant>` D-Bus error names, not the interface
/// surface), so — unlike the method/signal signatures — it can only be read
/// from source. zbus appends `.<Variant>`, so the attribute value carries no
/// trailing dot; the caller adds it to compare against the contract's
/// `error_prefix`. There is exactly one `#[zbus(prefix = …)]` in the file (the
/// `#[zbus::interface]` macro uses `name = …`, not `prefix`), so a plain search
/// is unambiguous; a loud panic fires if the attribute moves or is renamed.
fn parse_error_prefix() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/dbus_service.rs");
    let src = std::fs::read_to_string(path)
        .unwrap_or_else(|e| panic!("failed to read {path}: {e}"));
    let needle = "#[zbus(prefix = \"";
    let start = src.find(needle).unwrap_or_else(|| {
        panic!(
            "could not find `#[zbus(prefix = \"…\")]` in {path} — did the \
             HelperError prefix attribute move or change shape? Update this \
             parser (and reconcile the contract's error_prefix)."
        )
    }) + needle.len();
    let rest = &src[start..];
    let end = rest
        .find('"')
        .expect("unterminated `#[zbus(prefix = \"…` literal in dbus_service.rs");
    rest[..end].to_string()
}

// ── The live interface, introspected without a bus ──────────────────────

/// Construct a real `DbusService` over the crate's backend fakes. Introspection
/// reads only static metadata, so none of the fakes' behaviour is exercised —
/// they exist solely to satisfy the generic bounds and `Deps`.
fn build_interface() -> DbusService<FakeNetnsOps, FakeAuthorizer, FakeCaptiveCheck, FakeNameWatcher>
{
    let service = Arc::new(GatepathHelperService::new(Deps {
        ops: FakeNetnsOps::new(),
        auth: FakeAuthorizer::allow_all(),
        captive_check: FakeCaptiveCheck::new(),
        throttle: Throttle::new(100, Duration::from_secs(60)),
        watcher: FakeNameWatcher::new(),
        spawner: Box::new(FakeSpawner::new()),
        caller_uid_lookup: Box::new(FakeCallerUidLookup::new()),
        connectivity: Box::new(FakeNetnsConnectivity::new()),
        backstop: BackstopConfig {
            timer: Box::new(FakeBackstop::new()),
            duration: Duration::from_secs(30),
        },
        audit: Box::new(FakeAuditWriter::new()),
    }));
    DbusService::new(service)
}

// ── A minimal parser for the zbus introspection fragment ────────────────
//
// `introspect_to_writer` emits ONE `<interface>…</interface>` fragment (not
// the standard Introspectable/Peer/Properties interfaces — those are added by
// the ObjectServer node, not the interface impl). The output is line-oriented
// and machine-generated, so targeted attribute extraction is robust. Doc
// comments render as multi-line `<!-- … -->` blocks, which we skip.

#[derive(Debug, Default)]
struct ParsedMethod {
    inputs: Vec<String>,
    output: String,
}

#[derive(Debug, Default)]
struct ParsedInterface {
    name: String,
    methods: BTreeMap<String, ParsedMethod>,
    signals: BTreeMap<String, Vec<String>>,
}

/// Extract the value of `key="…"` from a single XML tag line.
fn attr<'a>(tag: &'a str, key: &str) -> Option<&'a str> {
    let needle = format!("{key}=\"");
    let start = tag.find(&needle)? + needle.len();
    let rest = &tag[start..];
    let end = rest.find('"')?;
    Some(&rest[..end])
}

enum Ctx {
    None,
    Method(String),
    Signal(String),
}

fn parse_introspection(xml: &str) -> ParsedInterface {
    let mut parsed = ParsedInterface::default();
    let mut ctx = Ctx::None;
    let mut in_comment = false;

    for raw in xml.lines() {
        let line = raw.trim();

        if in_comment {
            if line.contains("-->") {
                in_comment = false;
            }
            continue;
        }
        if line.starts_with("<!--") {
            if !line.contains("-->") {
                in_comment = true;
            }
            continue;
        }

        if line.starts_with("<interface ") {
            parsed.name = attr(line, "name").unwrap_or_default().to_string();
        } else if line.starts_with("<method ") {
            let name = attr(line, "name")
                .expect("<method> without a name attribute")
                .to_string();
            parsed.methods.insert(name.clone(), ParsedMethod::default());
            ctx = Ctx::Method(name);
        } else if line.starts_with("</method>") {
            ctx = Ctx::None;
        } else if line.starts_with("<signal ") {
            let name = attr(line, "name")
                .expect("<signal> without a name attribute")
                .to_string();
            parsed.signals.insert(name.clone(), Vec::new());
            ctx = Ctx::Signal(name);
        } else if line.starts_with("</signal>") {
            ctx = Ctx::None;
        } else if line.starts_with("<arg ") {
            let ty = attr(line, "type").unwrap_or_default().to_string();
            let dir = attr(line, "direction");
            match &ctx {
                Ctx::Method(m) => {
                    let entry = parsed
                        .methods
                        .get_mut(m)
                        .expect("<arg> inside an unregistered method");
                    match dir {
                        Some("in") => entry.inputs.push(ty),
                        // Multiple out args (none today) concatenate into one
                        // D-Bus signature string, matching the contract's `out`.
                        Some("out") => entry.output.push_str(&ty),
                        _ => {}
                    }
                }
                // Signal args carry no direction attribute.
                Ctx::Signal(s) => parsed
                    .signals
                    .get_mut(s)
                    .expect("<arg> inside an unregistered signal")
                    .push(ty),
                Ctx::None => {}
            }
        }
    }

    parsed
}

// ── The guard ───────────────────────────────────────────────────────────

#[test]
fn dbus_interface_matches_shared_contract() {
    let contract = load_contract();

    // Bus identifiers: the crate's public consts must match the artifact.
    assert_eq!(
        contract.bus_name, BUS_NAME,
        "bus_name drifted: contract {:?} vs crate const {:?}",
        contract.bus_name, BUS_NAME
    );
    assert_eq!(
        contract.object_path, OBJECT_PATH,
        "object_path drifted: contract {:?} vs crate const {:?}",
        contract.object_path, OBJECT_PATH
    );
    assert_eq!(
        contract.interface, INTERFACE,
        "interface name drifted: contract {:?} vs crate const {:?}",
        contract.interface, INTERFACE
    );

    let iface = build_interface();
    let mut xml = String::new();
    // No bus connection: introspection comes from the macro's static metadata.
    iface.introspect_to_writer(&mut xml, 0);
    let parsed = parse_introspection(&xml);

    assert_eq!(
        parsed.name, contract.interface,
        "introspected interface name disagrees with the contract.\n\
         introspection XML:\n{xml}"
    );

    // Method set — both directions, so additions AND removals are caught.
    let live_methods: BTreeSet<&String> = parsed.methods.keys().collect();
    let contract_methods: BTreeSet<&String> = contract.methods.keys().collect();
    assert_eq!(
        live_methods, contract_methods,
        "D-Bus METHOD SET drifted from the contract.\n\
         live (zbus):  {live_methods:?}\n\
         contract:     {contract_methods:?}\n\
         Did a method get added/removed/renamed? Update \
         docs/netns_helper_dbus_contract.json AND netns_client.py together.\n\n\
         introspection XML:\n{xml}"
    );

    for (name, spec) in &contract.methods {
        let live = &parsed.methods[name];
        assert_eq!(
            &live.inputs, &spec.inputs,
            "method `{name}` INPUT signature drifted: live {:?} vs contract {:?}.\n\
             Did the signature move? Reconcile dbus_service.rs, the contract, \
             and netns_client.py.",
            live.inputs, spec.inputs
        );
        assert_eq!(
            live.output, spec.out,
            "method `{name}` OUTPUT signature drifted: live {:?} vs contract {:?}.",
            live.output, spec.out
        );
    }

    // Signal set + arg signatures.
    let live_signals: BTreeSet<&String> = parsed.signals.keys().collect();
    let contract_signals: BTreeSet<&String> = contract.signals.keys().collect();
    assert_eq!(
        live_signals, contract_signals,
        "D-Bus SIGNAL SET drifted from the contract.\n\
         live (zbus):  {live_signals:?}\n\
         contract:     {contract_signals:?}\n\n\
         introspection XML:\n{xml}"
    );

    for (name, spec) in &contract.signals {
        assert_eq!(
            &parsed.signals[name], &spec.args,
            "signal `{name}` arg signature drifted: live {:?} vs contract {:?}.",
            parsed.signals[name], spec.args
        );
    }
}

/// The wire error-name prefix isn't in the introspection XML, so guard the
/// Rust `#[zbus(prefix = …)]` literal against the contract's `error_prefix`
/// directly. Without this, a lone edit of the Rust prefix would change every
/// wire error name (`<prefix>.<Variant>`) with nothing failing in CI — the
/// Python side only checks its own `ERROR_PREFIX` const against the artifact,
/// and `test_netns_client.py` builds names from that Python const, not the Rust
/// literal. (Closes the LOW noted when the contract guard first landed.)
#[test]
fn dbus_error_prefix_matches_contract() {
    let contract = load_contract();
    // zbus emits `<prefix>.<Variant>`, so the source literal has no trailing
    // dot; the contract's error_prefix does. Reconcile by appending the dot.
    let rust_prefix = format!("{}.", parse_error_prefix());
    assert_eq!(
        rust_prefix, contract.error_prefix,
        "wire error-name prefix drifted: Rust `#[zbus(prefix)]` + '.' = {:?} \
         vs contract error_prefix {:?}. Reconcile dbus_service.rs, \
         docs/netns_helper_dbus_contract.json, and netns_client.py's ERROR_PREFIX.",
        rust_prefix, contract.error_prefix
    );
}
