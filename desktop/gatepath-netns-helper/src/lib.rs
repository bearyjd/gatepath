//! `gatepath-netns-helper` — privileged helper for Gatepath's desktop
//! network-namespace isolation (Phase 5 of the diagnostic plan).
//!
//! # What this crate does (Phase 5a)
//!
//! This is the **skeleton + interface-validation** slice. It ships:
//!
//! - Public type definitions for the D-Bus protocol the Python orchestrator
//!   will speak to (`SetupCaptiveRequest`, `SetupCaptiveResponse`,
//!   `TeardownCaptiveResponse`).
//! - Strict interface-name validation that refuses any non-WiFi or
//!   VPN-shaped interface name. This is the security spec — when a future
//!   Phase 5b ships the real syscalls, validation will gate every call.
//! - Pure-logic unit tests with no system dependencies.
//!
//! # What this crate will do (Phase 5b)
//!
//! - PolicyKit authorization on every D-Bus method (`zbus` + `polkit-sys`).
//! - Actual netns creation and interface migration via `nix` syscalls.
//! - NetworkManager integration to confirm the requested interface is
//!   currently flagged as captive — argument validation is not enough on
//!   its own; we re-check at every invocation.
//! - Audit-log entries that match the cross-platform schema.
//!
//! # Threat model
//!
//! The helper runs as `root` (or `CAP_NET_ADMIN`-privileged) on the host
//! system. The unprivileged Gatepath UI process talks to it via the system
//! D-Bus. The helper's authorization scope is intentionally narrow:
//!
//! **Allowed**:
//!   - Move *the WiFi interface NetworkManager has currently flagged as
//!     captive* into Gatepath's dedicated netns
//!   - Tear that netns down on portal sign-in or D-Bus name disconnect
//!
//! **Refused**:
//!   - Moving any non-WiFi-named interface (caught by [`validation`])
//!   - Moving any VPN/wireguard/tailscale interface
//!   - Modifying host routing tables
//!   - Spawning host-netns processes
//!   - Touching `/etc/resolv.conf`, nftables, or NetworkManager's other
//!     connections
//!
//! Worst-case behaviour from a compromised helper: the user's own captive
//! sign-in stops working. Other apps' VPN traffic is unaffected because the
//! helper has no authorization to touch their interfaces.

pub mod audit_log;
pub mod auth;
pub mod dbus_service;
pub mod netns;
pub mod network_manager;
pub mod policykit;
pub mod service;
pub mod throttle;
pub mod validation;

/// Request shape for the D-Bus method `SetupCaptiveNetns(interface_name: s)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SetupCaptiveRequest {
    pub interface_name: String,
}

/// Response shape for `SetupCaptiveNetns`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SetupCaptiveResponse {
    /// Helper accepted the request, validated the interface, performed the
    /// PolicyKit check, and moved the interface into the gatepath netns.
    Success {
        /// Path to the netns under `/var/run/netns/`. The Python orchestrator
        /// passes this to `nsenter --net=<path>` when spawning the WebKit
        /// subprocess.
        netns_path: String,
    },
    /// Helper refused the request. [`reason`] is a stable machine-readable
    /// identifier the UI can map to a localised user-facing message.
    Refused { reason: RefusalReason },
}

/// Stable identifiers for refusal cases. The UI maps these to the strings
/// shown to the user; keeping the wire format stable lets us evolve copy
/// without bumping the protocol. Variants are append-only — old clients
/// that don't know a new variant should treat it as a generic refusal.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefusalReason {
    /// The requested interface name failed [`validation::validate_interface_name`]
    /// OR the interface doesn't exist on the system at all (per `NetworkManager`'s
    /// device list). Both are "this name is not usable" from the user's POV.
    InvalidInterface,
    /// The interface exists but `NetworkManager` does not flag it as captive.
    NotCaptive,
    /// `NetworkManager` is still evaluating connectivity for this interface
    /// (state == `NM_CONNECTIVITY_UNKNOWN`). Distinct from `NotCaptive` so the
    /// UI can show "retry shortly" instead of "this isn't a captive network".
    Pending,
    /// `PolicyKit` denied authorisation for the calling user.
    Unauthorised,
    /// `NetworkManager` D-Bus call itself failed — service unreachable, schema
    /// mismatch, etc. Distinct from `KernelError` so the UI can suggest
    /// "is NetworkManager running?" instead of a generic kernel hint.
    BackendUnavailable,
    /// Kernel returned an error during the netns/interface migration.
    KernelError,
    /// A previous setup is still active and hasn't been torn down.
    AlreadyActive,
    /// Caller exceeded the per-sender rate limit. UI should back off and
    /// retry after a brief delay; it should NOT prompt the user again.
    /// Closes the prompt-fatigue DoS the devil's advocate review flagged.
    Throttled,
}

/// Response shape for `TeardownCaptiveNetns`. No request payload — the helper
/// tears down whatever it set up most recently (per-process; helper tracks
/// one active netns at a time, since Gatepath only ever has one captive
/// session in flight).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TeardownCaptiveResponse {
    Success,
    NotActive,
    KernelError,
}
