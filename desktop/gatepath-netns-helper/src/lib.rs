//! `gatepath-netns-helper` — privileged helper for Gatepath's desktop
//! network-namespace isolation (Phase 5 of the diagnostic plan).
//!
//! # What this crate ships
//!
//! The full pipeline is implemented and unit-tested: D-Bus protocol types,
//! strict interface-name validation, PolicyKit authorization on every method,
//! NetworkManager captive re-checks, named-netns creation/teardown via
//! `ip(8)`, the privileged spawn into the netns (`spawn.rs`), name-watch
//! auto-teardown, the backstop timer, and schema-matching audit-log entries.
//!
//! # Real-hardware status
//!
//! The two operations that the unit suite's fakes used to hide are now
//! implemented:
//!
//!   - **PHY move** (`netns.rs::move_interface`, was BLOCKER-DESK-001):
//!     resolves the wiphy for the validated interface from sysfs and moves
//!     the whole PHY with `iw phy <phyN> set netns name` — not the
//!     netdev-only `ip link set ... netns` the wireless stack rejects.
//!   - **In-netns connectivity** (`connectivity.rs`, was BLOCKER-DESK-002):
//!     brings the link up, runs `wpa_supplicant` to re-associate to the
//!     captive SSID, and runs a DHCP client to reacquire an address — then
//!     tears all of that down with the session.
//!
//! Two caveats remain, both tracked in `docs/BLOCKERS.md`: the privileged
//! exec paths (`iw`/`wpa_supplicant`/DHCP) are validated only on real Wi-Fi
//! hardware via the `--ignored` integration tests (the unit suite covers
//! command construction + orchestration through fakes), and **secured**
//! captive networks (WPA2-PSK/EAP) are not yet supported — only open SSIDs,
//! which is the overwhelming captive-portal case.
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
pub mod backstop;
pub mod caller_uid;
pub mod connectivity;
pub mod dbus_service;
pub mod name_watch;
pub mod netns;
pub mod network_manager;
pub mod policykit;
pub mod service;
pub mod spawn;
pub mod throttle;
pub mod validation;

// D-Bus contract drift guard: asserts the zbus interface matches the shared
// docs/netns_helper_dbus_contract.json (introspection, no bus). Test-only.
#[cfg(test)]
mod dbus_contract_test;

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
    /// Phase 5b.7: portal URL passed to `LaunchPortal` failed validation
    /// (non-http(s) scheme, control bytes, or unparseable per RFC 3986).
    InvalidPortalUrl,
    /// DESK-004: a client-supplied display env value (`WAYLAND_DISPLAY`,
    /// `DISPLAY`, or `XAUTHORITY`) failed validation (length, control byte,
    /// charset, or `DISPLAY`/`XAUTHORITY` shape).
    InvalidDisplayEnv,
    /// Phase 5b.7: caller invoked `LaunchPortal` without a prior successful
    /// `SetupCaptive`. Single-session enforcement.
    NoActiveSession,
    /// Phase 5b.7: caller's bus name doesn't match the sender that opened
    /// the active session. Prevents one client from launching subprocesses
    /// inside another client's session.
    SenderMismatch,
    /// Phase 5b.7 / DESK-003 C4: launching the portal WebView's transient
    /// `systemd-run` unit failed (bad URL, missing netns, or `systemd-run`
    /// itself failing). Distinct from `KernelError` so the audit log
    /// differentiates netns migration failure from process-spawn failure.
    SpawnFailed,
    /// DESK-002: the captive network is **secured** (WPA/WPA2/WPA3/WEP). The
    /// helper can only re-associate to open captive networks today, so it
    /// refuses up front — before moving the PHY — rather than tearing away
    /// the user's real Wi-Fi only to fail at DHCP. See `docs/BLOCKERS.md`.
    UnsupportedSecurity,
}

impl RefusalReason {
    /// Stable snake-case name for a refusal variant. Used in audit logs
    /// and the D-Bus error mapping so a single source of truth exists for
    /// the wire-visible names.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::InvalidInterface => "invalid_interface",
            Self::NotCaptive => "not_captive",
            Self::Pending => "pending",
            Self::Unauthorised => "unauthorised",
            Self::BackendUnavailable => "backend_unavailable",
            Self::KernelError => "kernel_error",
            Self::AlreadyActive => "already_active",
            Self::Throttled => "throttled",
            Self::InvalidPortalUrl => "invalid_portal_url",
            Self::InvalidDisplayEnv => "invalid_display_env",
            Self::NoActiveSession => "no_active_session",
            Self::SenderMismatch => "sender_mismatch",
            Self::SpawnFailed => "spawn_failed",
            Self::UnsupportedSecurity => "unsupported_security",
        }
    }
}

/// Request shape for the D-Bus method
/// `LaunchPortal(portal_url: s, wayland_display: s, x_display: s, x_authority: s) -> u`
/// (Phase 5b.7; display fields DESK-004). Helper validates the URL and the
/// display values, confirms the active session belongs to the calling sender,
/// and launches the WebView in a transient unit joined to the gatepath netns.
///
/// The three display fields are forwarded from the unprivileged UI so the
/// WebView can reach the compositor/X server; `""` = unset. `XDG_RUNTIME_DIR`
/// and `DBUS_SESSION_BUS_ADDRESS` are NOT carried here — the helper derives them
/// from the authenticated caller UID.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchPortalRequest {
    pub portal_url: String,
    pub wayland_display: String,
    pub x_display: String,
    pub x_authority: String,
}

/// Response shape for `LaunchPortal`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LaunchPortalResponse {
    Success {
        /// PID of the spawned subprocess. Caller can pass this to
        /// `KillPortal` to terminate before exit. The helper independently
        /// reaps the subprocess and emits `PortalSubprocessExited` when it
        /// actually exits.
        pid: u32,
    },
    Refused {
        reason: RefusalReason,
    },
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
