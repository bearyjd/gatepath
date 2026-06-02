//! In-netns connectivity bring-up (BLOCKER-DESK-002).
//!
//! Moving the Wi-Fi PHY into the gatepath netns ([`crate::netns`]) leaves the
//! interface **unassociated and address-less**: NetworkManager stays in the
//! host netns and can no longer manage the device, moving a connected wiphy
//! drops the L2 association on most drivers, and the DHCP lease does not
//! travel with the PHY. So before the portal WebView can load anything, the
//! helper must, *inside* the netns:
//!
//!   1. Bring the loopback and the Wi-Fi link up.
//!   2. Run its own `wpa_supplicant` to re-associate to the captive SSID.
//!   3. Run a DHCP client to reacquire an address + the gateway/portal route.
//!
//! …and tear all three down when the session ends. This module owns that
//! lifecycle behind the [`NetnsConnectivity`] trait so the orchestrator
//! ([`crate::service`]) can drive it without root and tests can substitute
//! [`FakeNetnsConnectivity`].
//!
//! ## What is — and isn't — covered here
//!
//! The command **construction** (wpa_supplicant config rendering, `ip`/
//! supplicant/DHCP argv) is pure and unit-tested below. The actual process
//! execution in [`LinuxNetnsConnectivity`] requires real Wi-Fi hardware plus
//! `iw`, `wpa_supplicant`, and a DHCP client on the host, so — like the rest
//! of the privileged kernel surface — it is exercised only by the
//! `--ignored` integration path, never by the unit suite.
//!
//! ## Scope limitation: open networks only (for now)
//!
//! Captive portals are overwhelmingly **open** SSIDs (`key_mgmt=NONE`). This
//! MVP re-associates open networks only. Secured captive networks (WPA2-PSK,
//! enterprise EAP) would require lifting the PSK/credentials out of
//! NetworkManager's secret store, which is a separate, security-sensitive
//! piece of work; [`bring_up`](NetnsConnectivity::bring_up) returns
//! [`ConnectivityError::Unsupported`] for them rather than silently producing
//! a session that can never associate.

use std::path::{Path, PathBuf};

use thiserror::Error;

/// Runtime directory for per-session wpa_supplicant configs and pidfiles.
/// Root-owned, `0700`. Lives on a tmpfs in production (`/run`).
pub const RUNTIME_DIR: &str = "/run/gatepath";

/// Wi-Fi security of the captive network being re-joined. Only [`Open`] is
/// supported today; the secured variants are placeholders that keep the
/// match sites honest (and the wire ready) for when credential capture lands.
///
/// [`Open`]: WifiSecurity::Open
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WifiSecurity {
    /// Open network — no key management (`key_mgmt=NONE`).
    Open,
    /// WPA2/WPA3 personal. Carries the pre-shared key. **Not yet wired** end
    /// to end — present so [`render_wpa_config`] and the orchestrator can be
    /// extended without an enum-shape break.
    Psk(String),
}

/// Everything [`NetnsConnectivity::bring_up`] needs to re-establish the link.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConnectivityParams {
    /// Named netns the PHY was moved into (e.g. `gatepath`).
    pub netns_name: String,
    /// Wireless interface name inside the netns (e.g. `wlan0`).
    pub interface: String,
    /// SSID captured from NetworkManager *before* the PHY was moved.
    pub ssid: String,
    /// Security of the captive network.
    pub security: WifiSecurity,
}

/// Failure modes for connectivity bring-up. All are fatal to the session: on
/// any error the caller tears the netns down (which, together with dropping
/// the partially-built session, stops any child processes already spawned).
#[derive(Debug, Error)]
pub enum ConnectivityError {
    #[error("network '{ssid}' is not open; secured captive networks are not yet supported")]
    Unsupported { ssid: String },
    #[error("preparing runtime dir/config at {path}: {detail}")]
    RuntimeFile { path: String, detail: String },
    #[error("bringing up link '{interface}' in netns '{netns}': {detail}")]
    LinkUp {
        netns: String,
        interface: String,
        detail: String,
    },
    #[error("wpa_supplicant failed to associate in netns '{netns}': {detail}")]
    Supplicant { netns: String, detail: String },
    #[error("DHCP client failed to acquire a lease in netns '{netns}': {detail}")]
    Dhcp { netns: String, detail: String },
}

/// A live connectivity session. **Dropping it tears down** the in-netns
/// wpa_supplicant + DHCP client (and removes their runtime files). The trait
/// is a marker so the concrete teardown lives in each implementor's `Drop`.
///
/// The orchestrator stores the boxed session for the lifetime of the captive
/// session and drops it — before destroying the netns — on every teardown
/// path (explicit, sender-disconnect, and backstop).
///
/// `Debug` is required so the orchestrator (and tests) can log/inspect a
/// `Box<dyn ConnectivitySession>` without downcasting.
pub trait ConnectivitySession: Send + Sync + std::fmt::Debug {}

/// The privileged connectivity surface. Production wiring uses
/// [`LinuxNetnsConnectivity`]; tests use [`FakeNetnsConnectivity`].
pub trait NetnsConnectivity: Send + Sync {
    /// Inside the named netns: bring the link up, associate to the captive
    /// SSID, and acquire a DHCP lease. Returns a session handle whose `Drop`
    /// stops the supplicant and DHCP client.
    ///
    /// # Errors
    ///
    /// See [`ConnectivityError`]. Implementations MUST leave no running child
    /// processes behind on the error path.
    fn bring_up(
        &self,
        params: &ConnectivityParams,
    ) -> Result<Box<dyn ConnectivitySession>, ConnectivityError>;
}

// ── Pure command construction (unit-tested) ─────────────────────────────────

/// Render a minimal `wpa_supplicant` config for the captive network.
///
/// The SSID is emitted as an **unquoted hex string** (`ssid=<hex>`), the
/// wpa_supplicant form for binary SSIDs. That sidesteps all quote/backslash
/// escaping and means an attacker-influenced SSID (it comes from the AP
/// beacon) can never break out of the value into another config directive.
/// `scan_ssid=1` lets us find hidden/edge-case captive SSIDs.
///
/// Returns `None` for [`WifiSecurity::Psk`] — secured networks aren't wired
/// yet (callers map this to [`ConnectivityError::Unsupported`]).
pub fn render_wpa_config(ssid: &str, security: &WifiSecurity) -> Option<String> {
    match security {
        WifiSecurity::Open => Some(format!(
            "network={{\n\tscan_ssid=1\n\tssid={}\n\tkey_mgmt=NONE\n}}\n",
            to_hex(ssid.as_bytes()),
        )),
        WifiSecurity::Psk(_) => None,
    }
}

fn to_hex(bytes: &[u8]) -> String {
    use std::fmt::Write as _;
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        let _ = write!(s, "{b:02x}");
    }
    s
}

/// `ip` argv to bring an interface up inside a named netns:
/// `ip -n <netns> link set dev <dev> up`.
pub fn link_up_args<'a>(netns: &'a str, dev: &'a str) -> [&'a str; 7] {
    ["-n", netns, "link", "set", "dev", dev, "up"]
}

/// `ip` argv that runs `wpa_supplicant` in the foreground inside the netns:
/// `ip netns exec <netns> wpa_supplicant -D nl80211 -i <iface> -c <conf>`.
///
/// Foreground (no `-B`) on purpose: the spawned process becomes a tracked
/// child whose PID we can signal on teardown, rather than a daemon we'd have
/// to chase through a pidfile.
pub fn wpa_supplicant_args(netns: &str, interface: &str, conf_path: &Path) -> Vec<String> {
    vec![
        "netns".into(),
        "exec".into(),
        netns.into(),
        "wpa_supplicant".into(),
        "-D".into(),
        "nl80211".into(),
        "-i".into(),
        interface.into(),
        "-c".into(),
        conf_path.to_string_lossy().into_owned(),
    ]
}

/// Supported DHCP clients. Different distros ship different ones; the helper
/// picks whichever is present at deploy time. All are run in the foreground
/// so they stay tracked children.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DhcpClient {
    /// ISC `dhclient` (`-d` foreground, `-1` give up after one attempt).
    Dhclient,
    /// BusyBox `udhcpc` (`-f` foreground, `-q` exit after lease, `-n` give up
    /// if no lease).
    Udhcpc,
}

/// Bounded wait for a DHCP lease. The client is run **one-shot** (it exits
/// after acquiring a lease or giving up), so the orchestrator can't be wedged
/// holding the session lock indefinitely behind a dead AP. Kept short because
/// a captive portal that doesn't answer DHCP promptly isn't usable anyway.
const DHCP_TIMEOUT_SECS: u32 = 30;

/// `ip` argv that runs the chosen DHCP client **one-shot, in the foreground**
/// inside the netns: `ip netns exec <netns> <client ...> <iface>`. One-shot so
/// [`LinuxNetnsConnectivity::bring_up`] can wait on it and turn "did we get an
/// address" into a real success/failure signal (rather than reporting success
/// on mere process launch), and time-bounded so a non-answering network fails
/// instead of hanging.
pub fn dhcp_args(client: DhcpClient, netns: &str, interface: &str) -> Vec<String> {
    let mut args: Vec<String> = vec!["netns".into(), "exec".into(), netns.into()];
    match client {
        DhcpClient::Dhclient => {
            args.extend([
                "dhclient".into(),
                "-d".into(),
                "-1".into(),
                "-timeout".into(),
                DHCP_TIMEOUT_SECS.to_string(),
            ]);
        }
        DhcpClient::Udhcpc => {
            args.extend([
                "udhcpc".into(),
                "-f".into(),
                "-q".into(),
                "-n".into(),
                "-t".into(),
                "6".into(),
                "-i".into(),
                interface.into(),
            ]);
            return args;
        }
    }
    args.push(interface.into());
    args
}

fn runtime_conf_path(runtime_dir: &Path, netns_name: &str) -> PathBuf {
    runtime_dir.join(format!("wpa-{netns_name}.conf"))
}

// ── Production impl ─────────────────────────────────────────────────────────

use std::process::{Child, Command};

/// Production [`NetnsConnectivity`]. Spawns `wpa_supplicant` and a DHCP client
/// as tracked foreground children inside the netns via `ip netns exec`.
///
/// Integration-only: requires real Wi-Fi hardware + `iw`/`wpa_supplicant`/DHCP
/// client on the host. The unit suite covers the pure builders above and the
/// orchestration via [`FakeNetnsConnectivity`].
pub struct LinuxNetnsConnectivity {
    ip_binary: PathBuf,
    dhcp_client: DhcpClient,
    runtime_dir: PathBuf,
}

impl LinuxNetnsConnectivity {
    pub fn new() -> Self {
        Self {
            ip_binary: PathBuf::from("ip"),
            dhcp_client: DhcpClient::Dhclient,
            runtime_dir: PathBuf::from(RUNTIME_DIR),
        }
    }

    fn run_ip(&self, args: &[&str]) -> Result<(), String> {
        let output = Command::new(&self.ip_binary)
            .args(args)
            .output()
            .map_err(|e| format!("exec ip: {e}"))?;
        if output.status.success() {
            Ok(())
        } else {
            Err(String::from_utf8_lossy(&output.stderr).into_owned())
        }
    }

    /// Run `ip` to completion with owned-string argv (for the `Vec<String>`
    /// builders). Blocks until the command exits.
    fn run_ip_args(&self, args: &[String]) -> Result<(), String> {
        let argv: Vec<&str> = args.iter().map(String::as_str).collect();
        self.run_ip(&argv)
    }

    fn spawn_ip(&self, args: &[String]) -> Result<Child, String> {
        Command::new(&self.ip_binary)
            .args(args)
            .spawn()
            .map_err(|e| format!("spawn ip: {e}"))
    }
}

impl Default for LinuxNetnsConnectivity {
    fn default() -> Self {
        Self::new()
    }
}

impl NetnsConnectivity for LinuxNetnsConnectivity {
    fn bring_up(
        &self,
        params: &ConnectivityParams,
    ) -> Result<Box<dyn ConnectivitySession>, ConnectivityError> {
        let netns = &params.netns_name;

        // Secured networks aren't supported yet — fail loudly rather than
        // spawn a supplicant that can never associate.
        let conf_body = render_wpa_config(&params.ssid, &params.security).ok_or_else(|| {
            ConnectivityError::Unsupported {
                ssid: params.ssid.clone(),
            }
        })?;

        // Runtime dir (0700) + per-session config (0600). Both root-owned, but
        // tight perms keep the SSID (and future credentials) private. We both
        // create with mode 0700 AND set_permissions afterwards, because the
        // mode argument only applies on creation — set_permissions tightens a
        // dir that some earlier stage left world-readable.
        ensure_private_dir(&self.runtime_dir).map_err(|detail| ConnectivityError::RuntimeFile {
            path: self.runtime_dir.display().to_string(),
            detail,
        })?;
        let conf_path = runtime_conf_path(&self.runtime_dir, netns);
        write_private(&conf_path, &conf_body).map_err(|detail| ConnectivityError::RuntimeFile {
            path: conf_path.display().to_string(),
            detail,
        })?;

        // Bring loopback + the Wi-Fi link up inside the netns.
        for dev in ["lo", params.interface.as_str()] {
            self.run_ip(&link_up_args(netns, dev))
                .map_err(|detail| ConnectivityError::LinkUp {
                    netns: netns.clone(),
                    interface: dev.to_string(),
                    detail,
                })?;
        }

        // Build the session up front so that if anything below errors, the
        // returned-early Drop reaps whatever we already spawned.
        let mut session = LinuxConnectivitySession {
            children: Vec::new(),
            conf_path: Some(conf_path.clone()),
            ip_binary: self.ip_binary.clone(),
            netns: netns.clone(),
        };

        // wpa_supplicant runs for the whole session — a long-lived tracked
        // child the session signals on teardown.
        let wpa = self
            .spawn_ip(&wpa_supplicant_args(netns, &params.interface, &conf_path))
            .map_err(|detail| ConnectivityError::Supplicant {
                netns: netns.clone(),
                detail,
            })?;
        session.children.push(wpa);

        // The DHCP client is one-shot: run it to COMPLETION so its exit status
        // is a real "did we get an address" signal. Without this, bring_up
        // would report success on mere process launch and the WebView would
        // load into a netns with no L2 association and no IP. A non-zero exit
        // (which also covers "wpa_supplicant never associated", since DHCP
        // can't complete without L2) returns `Dhcp` — and the early return
        // drops `session`, killing wpa_supplicant. The DHCP client has already
        // exited, so it is NOT stored as a tracked child.
        self.run_ip_args(&dhcp_args(self.dhcp_client, netns, &params.interface))
            .map_err(|detail| ConnectivityError::Dhcp {
                netns: netns.clone(),
                detail,
            })?;

        Ok(Box::new(session))
    }
}

/// Create `dir` (recursively) with mode `0700` and tighten it to `0700` even
/// if it already existed with looser perms. Root-owned in production.
fn ensure_private_dir(dir: &Path) -> Result<(), String> {
    use std::os::unix::fs::{DirBuilderExt as _, PermissionsExt as _};
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(dir)
        .map_err(|e| e.to_string())?;
    std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())
}

/// Write `contents` to `path` with `0600` perms (create or truncate).
fn write_private(path: &Path, contents: &str) -> Result<(), String> {
    use std::io::Write as _;
    use std::os::unix::fs::OpenOptionsExt as _;
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| e.to_string())?;
    f.write_all(contents.as_bytes()).map_err(|e| e.to_string())
}

/// Concrete session: owns the supplicant/DHCP children and the config file.
#[derive(Debug)]
struct LinuxConnectivitySession {
    children: Vec<Child>,
    conf_path: Option<PathBuf>,
    ip_binary: PathBuf,
    netns: String,
}

impl ConnectivitySession for LinuxConnectivitySession {}

impl Drop for LinuxConnectivitySession {
    fn drop(&mut self) {
        // Stop the long-lived tracked child(ren) — wpa_supplicant — first so
        // they release their netns sockets (a process still pinned to the
        // netns would keep it alive past the orchestrator's `ip netns del`).
        for child in &mut self.children {
            let _ = child.kill();
            let _ = child.wait();
        }
        // Then sweep anything still pinned to the netns (a DHCP client that
        // re-exec'd a hook, say). Best-effort — the orchestrator's netns
        // teardown is the ultimate backstop.
        let _ = kill_netns_stragglers(&self.ip_binary, &self.netns);
        if let Some(path) = &self.conf_path {
            let _ = std::fs::remove_file(path);
        }
    }
}

/// Reap every process still pinned to the netns, escalating SIGTERM → SIGKILL.
/// Implemented without a shell (`ip netns pids` then `kill`), and without
/// `unsafe` (the crate denies it outside `spawn.rs`), so it reuses `kill(1)`
/// rather than a raw syscall. PIDs are enumerated in the host PID namespace;
/// the parse rejects non-numeric lines, so crafted stdout can't inject args.
fn kill_netns_stragglers(ip_binary: &Path, netns: &str) -> Result<(), String> {
    for signal in ["-TERM", "-KILL"] {
        let out = Command::new(ip_binary)
            .args(["netns", "pids", netns])
            .output()
            .map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Ok(()); // netns likely already gone
        }
        let pids: Vec<String> = String::from_utf8_lossy(&out.stdout)
            .lines()
            .filter_map(|l| l.trim().parse::<i32>().ok())
            .map(|pid| pid.to_string())
            .collect();
        if pids.is_empty() {
            return Ok(());
        }
        for pid in &pids {
            let _ = Command::new("kill").arg(signal).arg(pid).status();
        }
        // Give SIGTERM a moment to land before re-listing for the SIGKILL pass.
        std::thread::sleep(std::time::Duration::from_millis(200));
    }
    Ok(())
}

// ── Fake impl for tests ─────────────────────────────────────────────────────

#[cfg(test)]
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
};

/// In-memory [`NetnsConnectivity`] for orchestration tests. Records every
/// bring-up and counts session teardowns (so tests can assert the
/// orchestrator drops the session on each teardown path).
#[cfg(test)]
pub struct FakeNetnsConnectivity {
    brought_up: Mutex<Vec<ConnectivityParams>>,
    teardown_count: Arc<AtomicUsize>,
    fail_next: Mutex<bool>,
}

#[cfg(test)]
#[derive(Debug)]
struct FakeSession {
    teardown_count: Arc<AtomicUsize>,
}

#[cfg(test)]
impl ConnectivitySession for FakeSession {}

#[cfg(test)]
impl Drop for FakeSession {
    fn drop(&mut self) {
        self.teardown_count.fetch_add(1, Ordering::SeqCst);
    }
}

#[cfg(test)]
impl FakeNetnsConnectivity {
    pub fn new() -> Self {
        Self {
            brought_up: Mutex::new(Vec::new()),
            teardown_count: Arc::new(AtomicUsize::new(0)),
            fail_next: Mutex::new(false),
        }
    }

    /// Params passed to every successful (and attempted) bring-up, in order.
    pub fn brought_up(&self) -> Vec<ConnectivityParams> {
        self.brought_up.lock().unwrap().clone()
    }

    /// How many sessions have been dropped (torn down) so far.
    pub fn teardown_count(&self) -> usize {
        self.teardown_count.load(Ordering::SeqCst)
    }

    /// Make the next `bring_up` fail with a `Supplicant` error.
    pub fn fail_bring_up(&self) {
        *self.fail_next.lock().unwrap() = true;
    }
}

#[cfg(test)]
impl Default for FakeNetnsConnectivity {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl NetnsConnectivity for FakeNetnsConnectivity {
    fn bring_up(
        &self,
        params: &ConnectivityParams,
    ) -> Result<Box<dyn ConnectivitySession>, ConnectivityError> {
        self.brought_up.lock().unwrap().push(params.clone());
        if std::mem::replace(&mut *self.fail_next.lock().unwrap(), false) {
            return Err(ConnectivityError::Supplicant {
                netns: params.netns_name.clone(),
                detail: "fake forced".into(),
            });
        }
        if let WifiSecurity::Psk(_) = params.security {
            return Err(ConnectivityError::Unsupported {
                ssid: params.ssid.clone(),
            });
        }
        Ok(Box::new(FakeSession {
            teardown_count: Arc::clone(&self.teardown_count),
        }))
    }
}

/// Lets tests pass an `Arc<FakeNetnsConnectivity>` where the service wants a
/// `Box<dyn NetnsConnectivity>`, while keeping a handle for assertions.
#[cfg(test)]
impl<T: NetnsConnectivity> NetnsConnectivity for Arc<T> {
    fn bring_up(
        &self,
        params: &ConnectivityParams,
    ) -> Result<Box<dyn ConnectivitySession>, ConnectivityError> {
        T::bring_up(self, params)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── wpa_supplicant config rendering ─────────────────────────────────────

    #[test]
    fn open_network_renders_key_mgmt_none() {
        let conf = render_wpa_config("CoffeeWiFi", &WifiSecurity::Open).expect("open renders");
        assert!(conf.contains("key_mgmt=NONE"), "conf:\n{conf}");
        assert!(conf.contains("scan_ssid=1"), "conf:\n{conf}");
        // "CoffeeWiFi" hex-encoded, unquoted.
        assert!(
            conf.contains("ssid=436f6666656557694669"),
            "expected hex ssid, conf:\n{conf}",
        );
        assert!(
            !conf.contains('"'),
            "ssid must be hex, never quoted:\n{conf}"
        );
    }

    #[test]
    fn ssid_with_metacharacters_cannot_break_out_of_value() {
        // A hostile SSID containing newlines/quotes/braces must end up as
        // inert hex, never as injected config directives.
        let hostile = "evil\"\n\tkey_mgmt=NONE\n}\nnetwork={\n";
        let conf = render_wpa_config(hostile, &WifiSecurity::Open).expect("renders");
        // Exactly one network block; the hostile braces are hex, not syntax.
        assert_eq!(conf.matches("network={").count(), 1, "conf:\n{conf}");
        assert!(
            !conf.contains("evil\""),
            "raw SSID leaked into conf:\n{conf}"
        );
    }

    #[test]
    fn secured_network_is_not_rendered() {
        assert!(render_wpa_config("Home", &WifiSecurity::Psk("hunter2".into())).is_none());
    }

    // ── argv builders ───────────────────────────────────────────────────────

    #[test]
    fn link_up_targets_named_netns() {
        assert_eq!(
            link_up_args("gatepath", "wlan0"),
            ["-n", "gatepath", "link", "set", "dev", "wlan0", "up"],
        );
    }

    #[test]
    fn wpa_supplicant_runs_foreground_in_netns() {
        let args = wpa_supplicant_args("gatepath", "wlan0", Path::new("/run/gatepath/wpa-x.conf"));
        assert_eq!(&args[0..3], &["netns", "exec", "gatepath"]);
        assert!(args.contains(&"wpa_supplicant".to_string()));
        assert!(args.contains(&"/run/gatepath/wpa-x.conf".to_string()));
        // Foreground: never daemonize with -B.
        assert!(
            !args.contains(&"-B".to_string()),
            "must not background: {args:?}"
        );
    }

    #[test]
    fn dhclient_args_run_one_shot_foreground_and_bounded() {
        let args = dhcp_args(DhcpClient::Dhclient, "gatepath", "wlan0");
        assert_eq!(&args[0..3], &["netns", "exec", "gatepath"]);
        assert!(args.contains(&"dhclient".to_string()));
        assert!(args.contains(&"-d".to_string()));
        // One-shot (-1) and time-bounded (-timeout) so a dead AP can't wedge it.
        assert!(args.contains(&"-1".to_string()));
        let t = args
            .iter()
            .position(|a| a == "-timeout")
            .expect("has -timeout");
        assert_eq!(args[t + 1], "30");
        assert_eq!(args.last().unwrap(), "wlan0");
    }

    #[test]
    fn udhcpc_args_name_the_interface_and_give_up_on_failure() {
        let args = dhcp_args(DhcpClient::Udhcpc, "gatepath", "wlp3s0");
        assert!(args.contains(&"udhcpc".to_string()));
        assert!(args.contains(&"-f".to_string()));
        // -n: exit (non-zero) if no lease, rather than retrying forever.
        assert!(args.contains(&"-n".to_string()));
        // udhcpc takes the iface via -i, not positionally.
        let i = args.iter().position(|a| a == "-i").expect("has -i");
        assert_eq!(args[i + 1], "wlp3s0");
    }

    #[test]
    fn conf_path_is_per_netns_under_runtime_dir() {
        assert_eq!(
            runtime_conf_path(Path::new("/run/gatepath"), "gatepath"),
            Path::new("/run/gatepath/wpa-gatepath.conf"),
        );
    }

    // ── Fake behaviour ──────────────────────────────────────────────────────

    fn params(security: WifiSecurity) -> ConnectivityParams {
        ConnectivityParams {
            netns_name: "gatepath".into(),
            interface: "wlan0".into(),
            ssid: "CoffeeWiFi".into(),
            security,
        }
    }

    #[test]
    fn fake_records_bring_up_and_counts_teardown_on_drop() {
        let conn = FakeNetnsConnectivity::new();
        let session = conn
            .bring_up(&params(WifiSecurity::Open))
            .expect("bring up");
        assert_eq!(conn.brought_up().len(), 1);
        assert_eq!(conn.brought_up()[0].ssid, "CoffeeWiFi");
        assert_eq!(conn.teardown_count(), 0);
        drop(session);
        assert_eq!(
            conn.teardown_count(),
            1,
            "dropping the session must tear down"
        );
    }

    #[test]
    fn fake_forced_failure_surfaces_error_and_leaves_no_session() {
        let conn = FakeNetnsConnectivity::new();
        conn.fail_bring_up();
        let err = conn.bring_up(&params(WifiSecurity::Open)).unwrap_err();
        assert!(matches!(err, ConnectivityError::Supplicant { .. }));
        assert_eq!(conn.teardown_count(), 0);
    }

    #[test]
    fn fake_rejects_secured_network() {
        let conn = FakeNetnsConnectivity::new();
        let err = conn
            .bring_up(&params(WifiSecurity::Psk("pw".into())))
            .unwrap_err();
        assert!(matches!(err, ConnectivityError::Unsupported { .. }));
    }
}
