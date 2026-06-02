//! Netns and interface migration operations.
//!
//! The helper's privileged work boils down to three kernel operations:
//!
//!   1. Create a named network namespace (`/var/run/netns/<name>`) — gives
//!      Gatepath an isolated stack that other processes don't see.
//!   2. Move a WiFi interface from the host netns into that named netns.
//!   3. Tear the netns down on portal sign-in or app exit.
//!
//! All three require `CAP_NET_ADMIN`, which is why this lives in the
//! helper. Netns create/destroy use `ip(8)` from `iproute2`; the interface
//! move uses `iw(8)` from the wireless tools. Both ship in standard runtimes,
//! the command shapes are well-understood, and the strict interface-name
//! validator (see [`super::validation`]) prevents argument injection at the
//! one place where caller-supplied data enters a command line.
//!
//! ## Why the move uses `iw`, not `ip link`
//!
//! A wireless netdev (`wlan0`) is bound to its underlying PHY (`phyN`) and
//! **cannot** be moved between network namespaces on its own:
//! `ip link set dev wlan0 netns gatepath` returns `-EOPNOTSUPP` on real
//! Wi-Fi hardware. The wireless stack requires moving the **whole PHY** via
//! `iw phy <phyN> set netns name <name>` (the `nl80211`
//! `NL80211_CMD_SET_WIPHY_NETNS` operation). [`LinuxNetnsOps::move_interface`]
//! resolves the owning PHY for the validated interface from sysfs, then moves
//! that PHY. (This closes BLOCKER-DESK-001; see `docs/BLOCKERS.md`.)
//!
//! Moving the PHY leaves the interface **unassociated and address-less** in
//! the new netns — re-establishing connectivity (association + DHCP) is a
//! separate concern handled by [`super::connectivity`].
//!
//! The [`NetnsOps`] trait abstracts the kernel surface so unit tests can
//! drive the orchestrator without root. Production wiring uses
//! [`LinuxNetnsOps`]; tests use [`FakeNetnsOps`].

use std::path::{Path, PathBuf};
use std::process::Command;
use thiserror::Error;

/// Standard mount path for a named netns. The kernel maintains a bind mount
/// at this path; opening it gives a file descriptor that can be passed to
/// `setns(2)` or used with `nsenter`.
pub const NETNS_DIR: &str = "/var/run/netns";

/// Failure modes for kernel operations. Each variant carries enough context
/// to land in an audit-log entry without needing to look at the helper's
/// own logs.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum NetnsError {
    #[error("netns name '{0}' is empty or contains an invalid character")]
    InvalidName(String),
    #[error("netns '{name}' already exists")]
    AlreadyExists { name: String },
    #[error("netns '{name}' does not exist")]
    NotFound { name: String },
    #[error("`ip` command failed creating netns '{name}': {stderr}")]
    CreateFailed { name: String, stderr: String },
    #[error("could not resolve the wiphy for interface '{interface}': {detail}")]
    PhyResolutionFailed { interface: String, detail: String },
    #[error("`iw` command failed moving interface '{interface}' (phy) into '{netns}': {stderr}")]
    MoveFailed {
        interface: String,
        netns: String,
        stderr: String,
    },
    #[error("`ip` command failed tearing down netns '{name}': {stderr}")]
    TeardownFailed { name: String, stderr: String },
}

/// The three kernel operations the helper drives. Implementations:
/// - [`LinuxNetnsOps`] for production (shells out to `ip`).
/// - [`FakeNetnsOps`] for tests (in-memory).
pub trait NetnsOps {
    /// Create a named netns at `/var/run/netns/<name>`. Returns the path
    /// the caller can pass to `nsenter --net=<path> ...`.
    ///
    /// # Errors
    ///
    /// - [`NetnsError::InvalidName`] if `name` fails [`validate_netns_name`].
    /// - [`NetnsError::AlreadyExists`] if a netns with that name already exists.
    /// - [`NetnsError::CreateFailed`] if `ip` returned non-zero.
    fn create_netns(&self, name: &str) -> Result<PathBuf, NetnsError>;

    /// Move the named WiFi interface into the named gatepath netns by
    /// relocating its owning PHY (see the module docs for why the whole PHY,
    /// not the netdev, must move).
    ///
    /// # Errors
    ///
    /// - [`NetnsError::PhyResolutionFailed`] if the interface has no
    ///   resolvable wiphy (e.g. it isn't a wireless device, or it vanished
    ///   between validation and this call).
    /// - [`NetnsError::MoveFailed`] if `iw` returned non-zero (usually
    ///   because the netns doesn't exist or the kernel refused the move).
    ///
    /// **Caller responsibility**: validate `interface` via
    /// [`super::validation::validate_interface_name`] BEFORE calling this.
    /// The Linux impl does not re-validate; the trait is the privileged
    /// surface and assumes input has already been gated.
    fn move_interface(&self, interface: &str, netns_name: &str) -> Result<(), NetnsError>;

    /// Tear down the named netns. Idempotent — returns Ok if the netns
    /// doesn't exist.
    ///
    /// # Errors
    ///
    /// - [`NetnsError::TeardownFailed`] if `ip` returned non-zero for any
    ///   reason other than "netns doesn't exist".
    fn destroy_netns(&self, name: &str) -> Result<(), NetnsError>;
}

/// Validate a netns name. Helper limits the name space to a single fixed
/// value (`gatepath`) at the orchestration layer, but this validator is
/// stricter-than-necessary so that a future "per-network" netns naming
/// scheme can reuse it.
///
/// # Errors
///
/// Returns an error string when the name is empty, too long, or contains
/// anything outside `[a-zA-Z0-9_-]`.
pub fn validate_netns_name(name: &str) -> Result<(), String> {
    if name.is_empty() {
        return Err("netns name was empty".into());
    }
    // Linux limits namespace mount-name to 256 chars; we allow a much
    // shorter ceiling because /var/run/netns/<name> + format strings hit
    // PATH_MAX much earlier.
    if name.len() > 64 {
        return Err(format!("netns name '{name}' is too long (max 64 chars)"));
    }
    if !name
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return Err(format!("netns name '{name}' contains invalid characters"));
    }
    Ok(())
}

// ── Linux impl ──────────────────────────────────────────────────────────

/// sysfs path that exposes the wiphy name (`phyN`) for a wireless netdev.
/// The kernel maintains `phy80211/name` for every cfg80211 interface;
/// reading it is the canonical, parse-free way to map `wlan0` → `phy0`.
///
/// `interface` is assumed to have passed [`super::validation::validate_interface_name`]
/// (only `[A-Za-z0-9_-]`), so it can't introduce `..` or `/` path segments.
fn phy_name_sysfs_path(interface: &str) -> PathBuf {
    Path::new("/sys/class/net")
        .join(interface)
        .join("phy80211/name")
}

/// argv (after the `iw` binary) that moves a whole PHY into a named netns.
/// Pinned by a unit test so the DESK-001 fix can't silently regress back to
/// the netdev-only `ip link set ... netns` form.
fn phy_set_netns_args<'a>(phy: &'a str, netns_name: &'a str) -> [&'a str; 6] {
    ["phy", phy, "set", "netns", "name", netns_name]
}

/// Run a privileged command, mapping a non-zero exit (or exec failure) to
/// `(code, stderr)`. Shared by the `ip` and `iw` call paths.
fn run_command(binary: &Path, args: &[&str]) -> Result<(), (i32, String)> {
    let output = Command::new(binary)
        .args(args)
        .output()
        .map_err(|e| (-1, format!("could not exec {}: {e}", binary.display())))?;
    if output.status.success() {
        return Ok(());
    }
    let code = output.status.code().unwrap_or(-1);
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    Err((code, stderr))
}

/// Production [`NetnsOps`]. Uses `ip` from iproute2 for netns create/destroy
/// and `iw` from the wireless tools for the PHY move. Requires the helper
/// process to have `CAP_NET_ADMIN`; in deployment that's satisfied by
/// running as root via the systemd unit.
pub struct LinuxNetnsOps {
    /// Configurable for tests; defaults to `ip` on PATH.
    ip_binary: PathBuf,
    /// Configurable for tests; defaults to `iw` on PATH.
    iw_binary: PathBuf,
}

impl LinuxNetnsOps {
    pub fn new() -> Self {
        Self {
            ip_binary: PathBuf::from("ip"),
            iw_binary: PathBuf::from("iw"),
        }
    }

    fn run_ip(&self, args: &[&str]) -> Result<(), (i32, String)> {
        run_command(&self.ip_binary, args)
    }

    fn run_iw(&self, args: &[&str]) -> Result<(), (i32, String)> {
        run_command(&self.iw_binary, args)
    }

    /// Resolve `wlan0` → `phy0` by reading the sysfs `phy80211/name` link.
    /// Returns a human-readable detail string on failure (no such interface,
    /// not a wireless device, or an empty/unreadable attribute).
    fn resolve_phy(interface: &str) -> Result<String, String> {
        let path = phy_name_sysfs_path(interface);
        let raw = std::fs::read_to_string(&path)
            .map_err(|e| format!("reading {}: {e}", path.display()))?;
        let name = raw.trim();
        if name.is_empty() {
            return Err(format!("{} was empty", path.display()));
        }
        Ok(name.to_string())
    }
}

impl Default for LinuxNetnsOps {
    fn default() -> Self {
        Self::new()
    }
}

impl NetnsOps for LinuxNetnsOps {
    fn create_netns(&self, name: &str) -> Result<PathBuf, NetnsError> {
        validate_netns_name(name).map_err(|_| NetnsError::InvalidName(name.into()))?;
        let path = Path::new(NETNS_DIR).join(name);
        if path.exists() {
            return Err(NetnsError::AlreadyExists { name: name.into() });
        }
        self.run_ip(&["netns", "add", name])
            .map_err(|(_, stderr)| NetnsError::CreateFailed {
                name: name.into(),
                stderr,
            })?;
        Ok(path)
    }

    fn move_interface(&self, interface: &str, netns_name: &str) -> Result<(), NetnsError> {
        // DESK-001 fix: a Wi-Fi netdev cannot be moved between netns on its
        // own (`ip link set dev <iface> netns` → -EOPNOTSUPP). Resolve the
        // owning PHY, then move the whole PHY with
        // `iw phy <phyN> set netns name <netns>` (NL80211_CMD_SET_WIPHY_NETNS).
        //
        // We trust callers to have run validation::validate_interface_name
        // and validate_netns_name first. We do NOT re-validate here because
        // these strings have to flow through to `iw` verbatim and any
        // additional rejection logic creates two sources of truth. The
        // interface name's restricted charset is also what makes the sysfs
        // path lookup in `resolve_phy` injection-safe.
        let phy =
            Self::resolve_phy(interface).map_err(|detail| NetnsError::PhyResolutionFailed {
                interface: interface.into(),
                detail,
            })?;
        let args = phy_set_netns_args(&phy, netns_name);
        self.run_iw(&args)
            .map_err(|(_, stderr)| NetnsError::MoveFailed {
                interface: interface.into(),
                netns: netns_name.into(),
                stderr,
            })
    }

    fn destroy_netns(&self, name: &str) -> Result<(), NetnsError> {
        validate_netns_name(name).map_err(|_| NetnsError::InvalidName(name.into()))?;
        let path = Path::new(NETNS_DIR).join(name);
        if !path.exists() {
            // Idempotent — no work to do.
            return Ok(());
        }
        self.run_ip(&["netns", "del", name])
            .map_err(|(_, stderr)| NetnsError::TeardownFailed {
                name: name.into(),
                stderr,
            })
    }
}

// ── Fake impl for tests ─────────────────────────────────────────────────

/// In-memory [`NetnsOps`] for unit tests. Tracks created netns and moved
/// interfaces so tests can assert on the resulting state without root.
#[cfg(test)]
pub struct FakeNetnsOps {
    state: std::sync::Mutex<FakeState>,
}

#[cfg(test)]
#[derive(Default)]
struct FakeState {
    netns: Vec<String>,
    moved: Vec<(String, String)>, // (interface, netns_name)
}

#[cfg(test)]
impl FakeNetnsOps {
    pub fn new() -> Self {
        Self {
            state: std::sync::Mutex::new(FakeState::default()),
        }
    }

    pub fn netns(&self) -> Vec<String> {
        self.state.lock().unwrap().netns.clone()
    }

    pub fn moved(&self) -> Vec<(String, String)> {
        self.state.lock().unwrap().moved.clone()
    }
}

#[cfg(test)]
impl Default for FakeNetnsOps {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl NetnsOps for FakeNetnsOps {
    fn create_netns(&self, name: &str) -> Result<PathBuf, NetnsError> {
        validate_netns_name(name).map_err(|_| NetnsError::InvalidName(name.into()))?;
        let mut s = self.state.lock().unwrap();
        if s.netns.iter().any(|existing| existing == name) {
            return Err(NetnsError::AlreadyExists { name: name.into() });
        }
        s.netns.push(name.to_string());
        Ok(Path::new(NETNS_DIR).join(name))
    }

    fn move_interface(&self, interface: &str, netns_name: &str) -> Result<(), NetnsError> {
        let mut s = self.state.lock().unwrap();
        if !s.netns.iter().any(|existing| existing == netns_name) {
            return Err(NetnsError::MoveFailed {
                interface: interface.into(),
                netns: netns_name.into(),
                stderr: format!("fake: netns '{netns_name}' does not exist"),
            });
        }
        s.moved
            .push((interface.to_string(), netns_name.to_string()));
        Ok(())
    }

    fn destroy_netns(&self, name: &str) -> Result<(), NetnsError> {
        validate_netns_name(name).map_err(|_| NetnsError::InvalidName(name.into()))?;
        let mut s = self.state.lock().unwrap();
        s.netns.retain(|existing| existing != name);
        s.moved.retain(|(_, ns)| ns != name);
        Ok(())
    }
}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn netns_name_validation_accepts_canonical_name() {
        assert!(validate_netns_name("gatepath").is_ok());
        assert!(validate_netns_name("gatepath-1").is_ok());
        assert!(validate_netns_name("gatepath_test").is_ok());
    }

    #[test]
    fn netns_name_validation_rejects_invalid_input() {
        assert!(validate_netns_name("").is_err());
        assert!(validate_netns_name("has space").is_err());
        assert!(validate_netns_name("a/../etc").is_err());
        assert!(validate_netns_name("semi;colon").is_err());
        assert!(validate_netns_name(&"x".repeat(65)).is_err());
    }

    #[test]
    fn fake_create_returns_canonical_path() {
        let ops = FakeNetnsOps::new();
        let path = ops.create_netns("gatepath").expect("create");
        assert_eq!(path, Path::new("/var/run/netns/gatepath"));
        assert_eq!(ops.netns(), vec!["gatepath".to_string()]);
    }

    #[test]
    fn fake_create_twice_is_already_exists() {
        let ops = FakeNetnsOps::new();
        ops.create_netns("gatepath").expect("first");
        let err = ops.create_netns("gatepath").unwrap_err();
        assert_eq!(
            err,
            NetnsError::AlreadyExists {
                name: "gatepath".into()
            },
        );
    }

    #[test]
    fn fake_move_into_unknown_netns_fails_loudly() {
        let ops = FakeNetnsOps::new();
        let err = ops.move_interface("wlan0", "missing").unwrap_err();
        assert!(
            matches!(err, NetnsError::MoveFailed { .. }),
            "expected MoveFailed, got {err:?}",
        );
    }

    #[test]
    fn fake_move_records_pairing() {
        let ops = FakeNetnsOps::new();
        ops.create_netns("gatepath").expect("create");
        ops.move_interface("wlan0", "gatepath").expect("move");
        assert_eq!(
            ops.moved(),
            vec![("wlan0".to_string(), "gatepath".to_string())],
        );
    }

    #[test]
    fn fake_destroy_is_idempotent() {
        let ops = FakeNetnsOps::new();
        ops.destroy_netns("never-existed").expect("idempotent");
        ops.create_netns("gatepath").expect("create");
        ops.destroy_netns("gatepath").expect("destroy");
        ops.destroy_netns("gatepath").expect("destroy-again");
        assert!(ops.netns().is_empty());
    }

    #[test]
    fn fake_destroy_clears_associated_moves() {
        let ops = FakeNetnsOps::new();
        ops.create_netns("gatepath").expect("create");
        ops.move_interface("wlan0", "gatepath").expect("move");
        ops.destroy_netns("gatepath").expect("destroy");
        assert!(ops.moved().is_empty());
    }

    #[test]
    fn linux_create_validates_netns_name_first() {
        // Don't actually invoke `ip` — pass an invalid name so we short-
        // circuit on validation, never reaching the syscall.
        let ops = LinuxNetnsOps::new();
        let err = ops.create_netns("bad/name").unwrap_err();
        assert_eq!(err, NetnsError::InvalidName("bad/name".into()));
    }

    #[test]
    fn linux_destroy_validates_netns_name_first() {
        let ops = LinuxNetnsOps::new();
        let err = ops.destroy_netns("bad name").unwrap_err();
        assert_eq!(err, NetnsError::InvalidName("bad name".into()));
    }

    // ── DESK-001: PHY-move command construction ─────────────────────────────

    #[test]
    fn phy_sysfs_path_is_under_class_net() {
        // The interface name has already passed validation (charset
        // [A-Za-z0-9_-]), so it cannot inject `..`/`/` path segments.
        assert_eq!(
            phy_name_sysfs_path("wlan0"),
            Path::new("/sys/class/net/wlan0/phy80211/name"),
        );
        assert_eq!(
            phy_name_sysfs_path("wlp3s0"),
            Path::new("/sys/class/net/wlp3s0/phy80211/name"),
        );
    }

    #[test]
    fn phy_move_uses_iw_whole_phy_form_not_ip_link() {
        // Regression pin for BLOCKER-DESK-001: the move MUST target the
        // whole PHY (`iw phy <phyN> set netns name <netns>`), never the
        // netdev-only `ip link set dev <iface> netns <netns>` form that the
        // wireless stack rejects with -EOPNOTSUPP.
        assert_eq!(
            phy_set_netns_args("phy0", "gatepath"),
            ["phy", "phy0", "set", "netns", "name", "gatepath"],
        );
    }

    #[test]
    fn phy_move_args_carry_through_resolved_phy_and_netns() {
        let args = phy_set_netns_args("phy7", "gatepath-test");
        assert_eq!(args[1], "phy7");
        assert_eq!(args[5], "gatepath-test");
    }

    #[test]
    fn resolve_phy_errors_for_nonexistent_interface() {
        // No real wireless device under this name on a test host → the
        // sysfs read fails and we surface a detail string rather than panic.
        let err = LinuxNetnsOps::resolve_phy("wlx-does-not-exist-99")
            .expect_err("nonexistent interface must not resolve a phy");
        assert!(
            err.contains("/sys/class/net/wlx-does-not-exist-99/phy80211/name"),
            "detail should name the sysfs path, got: {err}",
        );
    }
}
