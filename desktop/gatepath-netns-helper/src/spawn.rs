//! Privileged subprocess spawn into the gatepath netns (Phase 5b.7, DESK-003 C4).
//!
//! The portal WebView must run (a) inside the gatepath netns, (b) as the
//! unprivileged calling user, and (c) with `MemoryDenyWriteExecute` (W^X)
//! relaxed — WebKitGTK JITs JavaScript and needs writable+executable pages.
//!
//! ## Why a transient systemd unit (C4)
//!
//! The helper proper runs with `MemoryDenyWriteExecute=yes` so the long-lived,
//! root, D-Bus-facing process cannot be coerced into mapping W+X memory. systemd
//! enforces that with a **seccomp filter that is inherited by every descendant
//! and can never be lifted** (seccomp is one-way by design). A WebView forked
//! directly from the helper would therefore inherit the filter and its JIT would
//! crash. We cannot "drop" W^X in the child.
//!
//! The fix is to not make the WebView a *descendant* of the helper at all.
//! [`LinuxSpawner`] launches it as a **transient systemd `.service`** via
//! `systemd-run`: PID 1 forks and exec's it, so it is born a *sibling* of the
//! helper without the helper's seccomp filter, and we set
//! `MemoryDenyWriteExecute=no` on that unit alone. The relaxation is scoped to
//! the one process that needs it; the privileged helper keeps W^X.
//!
//! A transient `.service` (not a `.scope`) is required: a scope wraps a process
//! the caller already forked, so PID 1 cannot apply exec-time settings
//! (`MemoryDenyWriteExecute=`, `NetworkNamespacePath=`, user switching) to it. A
//! `.service` is forked+exec'd by PID 1, which applies the full exec context.
//!
//! ## Trust surface
//!
//! Delegating to systemd also moves the namespace entry and privilege drop out
//! of hand-rolled post-fork syscalls and into PID 1's audited exec path:
//!
//! - **Netns entry** is declarative: `NetworkNamespacePath=` *joins* the
//!   already-created `/var/run/netns/gatepath`. It never creates a new netns,
//!   so a missing netns fails the unit rather than silently isolating the child.
//! - **Privilege drop** is `--uid`/`--gid` to the caller's identity; systemd
//!   resets supplementary groups too (the old in-process `setresuid` did not).
//! - **Hardcoded exec path** ([`PORTAL_RUNNER_PATH`]). No caller input reaches
//!   the command path; only the validated portal URL is passed, after `--` so
//!   it can never be mistaken for a `systemd-run` option.
//! - **One controlled argument**: the portal URL, validated against [`url::Url`]
//!   (RFC 3986) — `http`/`https` only, no control bytes, length-bounded.
//! - **Fail-closed**: any error spawning `systemd-run` aborts the launch; the
//!   audit log records the failure.
//!
//! ## Test strategy
//!
//! The `systemd-run` argument vector is built by the pure [`systemd_run_args`]
//! and pinned by unit tests (the same "pin the privileged argv" approach as
//! `netns.rs`'s `phy_set_netns_args`). The actual exec — `systemd-run` joining
//! the netns, dropping privilege, and the WebKit JIT running under
//! `MemoryDenyWriteExecute=no` — requires root + a real netns and is covered by
//! the `--ignored` suite in `tests/dbus_integration.rs` / on-hardware validation
//! (BLOCKER-DESK-003). Service-level tests use [`FakeSpawner`].

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use thiserror::Error;

/// Hardcoded exec path for the portal WebView runner. The helper refuses
/// to start if this file is missing — installers must place the runner
/// here regardless of how they ship the rest of the app. Flatpak users
/// do not get isolation; the system path is set up by the
/// distro-packaged helper, not by the Flatpak.
pub const PORTAL_RUNNER_PATH: &str = "/usr/lib/gatepath/portal-webview-runner";

/// Default `systemd-run` binary. Resolved via the unit's pinned `PATH`
/// (`/usr/sbin:/usr/bin:/sbin:/bin`), exactly like the helper's other execs
/// (`ip`, `iw`, `wpa_supplicant`, `kill`). Overridable on [`LinuxSpawner`] for
/// integration tests that point at a stub.
pub const SYSTEMD_RUN_BINARY: &str = "systemd-run";

/// Failure modes for the privileged spawn. Each variant maps to an
/// audit-log refusal reason and a D-Bus error name in the wire protocol.
#[derive(Debug, Error)]
pub enum SpawnError {
    #[error("portal URL invalid: {0}")]
    InvalidUrl(String),
    #[error("netns '{name}' is not present at {path:?}")]
    NetnsMissing { name: String, path: PathBuf },
    #[error("could not look up caller UID: {0}")]
    CallerUidUnavailable(String),
    #[error("launching systemd-run failed: {0}")]
    SyscallFailed(String),
}

/// Outcome of an exited child process. Delivered asynchronously via the
/// callback registered with [`Spawner::set_exit_callback`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SpawnExit {
    pub pid: u32,
    /// `Some` if the child exited normally; `None` if it was killed by a
    /// signal (in which case [`signal`] holds the signal number).
    pub exit_code: Option<i32>,
    pub signal: Option<i32>,
}

impl SpawnExit {
    #[must_use]
    pub fn is_clean(self) -> bool {
        matches!(self.exit_code, Some(0))
    }
}

/// Inputs to a single spawn. The helper assembles this from the validated
/// D-Bus request plus the caller's resolved UID.
#[derive(Debug, Clone)]
pub struct SpawnRequest {
    pub portal_url: String,
    pub netns_name: String,
    pub caller_uid: u32,
}

/// Privileged spawn surface. Real impl ([`LinuxSpawner`]) launches a transient
/// systemd unit; tests use [`FakeSpawner`].
pub trait Spawner: Send + Sync + 'static {
    /// Validate the request and launch the runner in a transient systemd unit
    /// joined to the netns and dropped to the caller's UID. Returns the
    /// controlling `systemd-run` PID on success.
    ///
    /// Implementations MUST:
    /// 1. Re-validate the portal URL (the trait is the privileged surface;
    ///    don't trust prior validation).
    /// 2. Fail closed if the target netns is absent.
    /// 3. Relax W^X for the child only — never for the helper.
    /// 4. Spawn a wait-thread that reaps the child and invokes the
    ///    exit callback, if registered.
    ///
    /// # Errors
    ///
    /// Returns the corresponding [`SpawnError`] variant on each failure
    /// mode. Caller maps to a [`crate::RefusalReason`] for the wire.
    fn spawn(&self, request: &SpawnRequest) -> Result<u32, SpawnError>;

    /// Register a callback invoked when the most recent spawned child
    /// exits. Replaces any prior callback. Set to `None` to disable.
    ///
    /// The callback runs on the spawner's wait-thread, NOT the calling
    /// thread. It must not block; orchestrators should forward the event
    /// onto a channel and process it elsewhere.
    fn set_exit_callback(&self, cb: Option<ExitCallback>);
}

pub type ExitCallback = Arc<dyn Fn(SpawnExit) + Send + Sync + 'static>;

// ── Production impl ────────────────────────────────────────────────────

use std::os::unix::process::ExitStatusExt as _;
use std::path::Path;
use std::process::Command;
use std::thread;

/// Production spawner. Holds the runner path and the `systemd-run` binary so
/// tests can substitute stubs; production wiring uses [`PORTAL_RUNNER_PATH`]
/// and [`SYSTEMD_RUN_BINARY`].
pub struct LinuxSpawner {
    runner_path: PathBuf,
    systemd_run_binary: PathBuf,
    exit_callback: Mutex<Option<ExitCallback>>,
}

impl LinuxSpawner {
    pub fn new(runner_path: impl Into<PathBuf>) -> Self {
        Self {
            runner_path: runner_path.into(),
            systemd_run_binary: PathBuf::from(SYSTEMD_RUN_BINARY),
            exit_callback: Mutex::new(None),
        }
    }

    /// Override the `systemd-run` binary. For integration tests that point at
    /// a stub; production uses the [`SYSTEMD_RUN_BINARY`] default.
    #[must_use]
    pub fn with_systemd_run_binary(mut self, binary: impl Into<PathBuf>) -> Self {
        self.systemd_run_binary = binary.into();
        self
    }
}

impl Spawner for LinuxSpawner {
    fn spawn(&self, request: &SpawnRequest) -> Result<u32, SpawnError> {
        validate_portal_url(&request.portal_url)?;

        // Fail closed if the netns isn't present. `systemd-run` would also
        // fail when `NetworkNamespacePath=` can't be opened, but a clean
        // NetnsMissing here keeps the wire error precise and avoids spawning
        // systemd-run only to have the unit fail.
        let netns_path = netns_mount_path(&request.netns_name);
        if !netns_path.exists() {
            return Err(SpawnError::NetnsMissing {
                name: request.netns_name.clone(),
                path: netns_path,
            });
        }

        let args = systemd_run_args(&self.runner_path, request);
        let child = Command::new(&self.systemd_run_binary)
            .args(&args)
            .spawn()
            .map_err(|e| SpawnError::SyscallFailed(format!("systemd-run spawn failed: {e}")))?;
        let pid = child.id();

        // Reap the controlling `systemd-run` on a dedicated thread and notify
        // the callback. `--wait` makes systemd-run block until the transient
        // unit exits and propagate its status, so this exit reflects the
        // WebView's outcome. (The reported PID is systemd-run's, not the
        // WebView's; teardown reaps by netns membership, not this PID.)
        let cb_holder = self.exit_callback.lock().unwrap().clone();
        thread::spawn(move || {
            let mut child = child;
            let exit = match child.wait() {
                Ok(status) => SpawnExit {
                    pid,
                    exit_code: status.code(),
                    signal: status.signal(),
                },
                Err(e) => {
                    tracing::error!(error = %e, "waiting on systemd-run failed");
                    SpawnExit {
                        pid,
                        exit_code: None,
                        signal: None,
                    }
                }
            };
            if let Some(cb) = cb_holder {
                cb(exit);
            }
        });

        Ok(pid)
    }

    fn set_exit_callback(&self, cb: Option<ExitCallback>) {
        *self.exit_callback.lock().unwrap() = cb;
    }
}

fn netns_mount_path(name: &str) -> PathBuf {
    Path::new(crate::netns::NETNS_DIR).join(name)
}

/// Build the `systemd-run` argument vector (excluding the binary itself) that
/// launches the portal WebView as a transient `.service`.
///
/// The shape is security-load-bearing and pinned by unit tests:
/// - `--wait --collect --quiet`: block until the unit exits and propagate its
///   status (so the wait-thread can deliver a [`SpawnExit`]); garbage-collect
///   the unit even on failure so repeated launches don't accumulate dead units;
///   suppress systemd-run's own chatter.
/// - `--property=MemoryDenyWriteExecute=no`: relax W^X for the JIT — this child
///   only. The helper proper keeps `MemoryDenyWriteExecute=yes`.
/// - `--property=NetworkNamespacePath=…`: **join** the existing gatepath netns;
///   never create a new one.
/// - `--uid`/`--gid`: drop to the caller's identity; the WebView is fully
///   unprivileged.
/// - `--`: terminate option parsing so the runner path and the (validated)
///   portal URL can never be interpreted as `systemd-run` options.
fn systemd_run_args(runner_path: &Path, request: &SpawnRequest) -> Vec<String> {
    let netns_path = netns_mount_path(&request.netns_name);
    vec![
        "--wait".to_string(),
        "--collect".to_string(),
        "--quiet".to_string(),
        "--property=MemoryDenyWriteExecute=no".to_string(),
        format!("--property=NetworkNamespacePath={}", netns_path.display()),
        format!("--uid={}", request.caller_uid),
        format!("--gid={}", request.caller_uid),
        "--".to_string(),
        runner_path.to_string_lossy().into_owned(),
        request.portal_url.clone(),
    ]
}

/// Validate a portal URL the helper is about to pass to the runner.
///
/// Constraints:
/// - Parses as RFC 3986 via [`url::Url`].
/// - Scheme is `http` or `https` (only). Captive portals never use
///   anything else; rejecting other schemes closes a class of attacks.
/// - No control bytes (`< 0x20` or `== 0x7F`). `url` accepts these in
///   percent-encoded form already; this catches raw bytes that snuck in.
/// - Length bounded to 4096 bytes; nothing legitimate is longer.
pub fn validate_portal_url(raw: &str) -> Result<(), SpawnError> {
    if raw.len() > 4096 {
        return Err(SpawnError::InvalidUrl("exceeds 4096 bytes".into()));
    }
    if let Some(bad) = raw.bytes().find(|&b| b < 0x20 || b == 0x7F) {
        return Err(SpawnError::InvalidUrl(format!(
            "contains control byte 0x{bad:02X}"
        )));
    }
    let url =
        url::Url::parse(raw).map_err(|e| SpawnError::InvalidUrl(format!("parse failed: {e}")))?;
    match url.scheme() {
        "http" | "https" => Ok(()),
        other => Err(SpawnError::InvalidUrl(format!(
            "scheme '{other}' not allowed"
        ))),
    }
}

// ── Fake impl for tests ────────────────────────────────────────────────

#[cfg(test)]
pub struct FakeSpawner {
    inner: Arc<Mutex<FakeInner>>,
}

#[cfg(test)]
struct FakeInner {
    /// Every request that came through `spawn`, in order.
    requests: Vec<SpawnRequest>,
    /// Next PID to hand out. Starts at 10_000 so it doesn't collide with
    /// real PIDs in any plausible test env.
    next_pid: u32,
    /// Names that should fail at spawn-time with a SyscallFailed error.
    /// Used to drive the failure path.
    fail_for_url: Option<String>,
    /// Most recent registered exit callback. Tests fire it via
    /// [`FakeSpawner::fire_exit`].
    exit_callback: Option<ExitCallback>,
}

#[cfg(test)]
impl FakeSpawner {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(FakeInner {
                requests: Vec::new(),
                next_pid: 10_000,
                fail_for_url: None,
                exit_callback: None,
            })),
        }
    }

    pub fn requests(&self) -> Vec<SpawnRequest> {
        self.inner.lock().unwrap().requests.clone()
    }

    pub fn fail_for_url(&self, url: &str) {
        self.inner.lock().unwrap().fail_for_url = Some(url.to_string());
    }

    /// Invoke the registered exit callback synchronously. Mirrors what the
    /// real spawner's wait-thread would do.
    pub fn fire_exit(&self, exit: SpawnExit) {
        let cb = self.inner.lock().unwrap().exit_callback.clone();
        if let Some(cb) = cb {
            cb(exit);
        }
    }
}

#[cfg(test)]
impl Default for FakeSpawner {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl Spawner for FakeSpawner {
    fn spawn(&self, request: &SpawnRequest) -> Result<u32, SpawnError> {
        validate_portal_url(&request.portal_url)?;
        let mut inner = self.inner.lock().unwrap();
        if let Some(target) = &inner.fail_for_url
            && target == &request.portal_url
        {
            return Err(SpawnError::SyscallFailed("fake forced".into()));
        }
        let pid = inner.next_pid;
        inner.next_pid += 1;
        inner.requests.push(request.clone());
        Ok(pid)
    }

    fn set_exit_callback(&self, cb: Option<ExitCallback>) {
        self.inner.lock().unwrap().exit_callback = cb;
    }
}

/// Lets tests pass an `Arc<FakeSpawner>` as the service's spawner while
/// keeping a separate handle for assertions. Service stores
/// `Box<dyn Spawner>`; both `LinuxSpawner` and `Arc<FakeSpawner>` satisfy
/// it.
impl<T: Spawner> Spawner for Arc<T> {
    fn spawn(&self, request: &SpawnRequest) -> Result<u32, SpawnError> {
        T::spawn(self, request)
    }
    fn set_exit_callback(&self, cb: Option<ExitCallback>) {
        T::set_exit_callback(self, cb);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(url: &str) -> SpawnRequest {
        SpawnRequest {
            portal_url: url.into(),
            netns_name: "gatepath".into(),
            caller_uid: 1000,
        }
    }

    // ── URL validation ──────────────────────────────────────────────────

    #[test]
    fn http_url_validates() {
        assert!(validate_portal_url("http://captive.example/login").is_ok());
    }

    #[test]
    fn https_url_validates() {
        assert!(validate_portal_url("https://captive.example/login").is_ok());
    }

    #[test]
    fn ip_literal_with_port_validates() {
        assert!(validate_portal_url("http://192.0.2.1:8080/captive").is_ok());
    }

    #[test]
    fn file_scheme_rejected() {
        let err = validate_portal_url("file:///etc/passwd").unwrap_err();
        assert!(matches!(err, SpawnError::InvalidUrl(_)));
    }

    #[test]
    fn javascript_scheme_rejected() {
        let err = validate_portal_url("javascript:alert(1)").unwrap_err();
        assert!(matches!(err, SpawnError::InvalidUrl(_)));
    }

    #[test]
    fn embedded_newline_rejected() {
        let err = validate_portal_url("http://example.com/\npath").unwrap_err();
        assert!(matches!(err, SpawnError::InvalidUrl(_)));
    }

    #[test]
    fn embedded_null_byte_rejected() {
        let err = validate_portal_url("http://example.com/\0path").unwrap_err();
        assert!(matches!(err, SpawnError::InvalidUrl(_)));
    }

    #[test]
    fn oversized_url_rejected() {
        let huge = format!("http://example.com/{}", "a".repeat(5000));
        let err = validate_portal_url(&huge).unwrap_err();
        assert!(matches!(err, SpawnError::InvalidUrl(_)));
    }

    #[test]
    fn malformed_url_rejected() {
        let err = validate_portal_url("not a url").unwrap_err();
        assert!(matches!(err, SpawnError::InvalidUrl(_)));
    }

    // ── systemd-run argv (C4): pin the privileged command shape ──────────

    #[test]
    fn systemd_run_args_pins_transient_service_shape() {
        let runner = Path::new("/usr/lib/gatepath/portal-webview-runner");
        let args = systemd_run_args(runner, &req("http://captive.example/login"));
        assert_eq!(
            args,
            vec![
                "--wait".to_string(),
                "--collect".to_string(),
                "--quiet".to_string(),
                "--property=MemoryDenyWriteExecute=no".to_string(),
                "--property=NetworkNamespacePath=/var/run/netns/gatepath".to_string(),
                "--uid=1000".to_string(),
                "--gid=1000".to_string(),
                "--".to_string(),
                "/usr/lib/gatepath/portal-webview-runner".to_string(),
                "http://captive.example/login".to_string(),
            ]
        );
    }

    #[test]
    fn systemd_run_args_relaxes_mdwe_for_child_only() {
        // The W^X relaxation must be expressed as a per-unit property — never
        // a global flag — so it cannot leak to the helper proper.
        let runner = Path::new(PORTAL_RUNNER_PATH);
        let args = systemd_run_args(runner, &req("https://captive.example/"));
        assert!(
            args.iter()
                .any(|a| a == "--property=MemoryDenyWriteExecute=no")
        );
    }

    #[test]
    fn systemd_run_args_joins_named_netns_not_a_new_one() {
        // `NetworkNamespacePath=` JOINS the existing netns. `PrivateNetwork=`
        // would create a fresh, empty one — that must never appear.
        let runner = Path::new(PORTAL_RUNNER_PATH);
        let args = systemd_run_args(runner, &req("http://captive.example/"));
        assert!(
            args.iter()
                .any(|a| a == "--property=NetworkNamespacePath=/var/run/netns/gatepath")
        );
        assert!(!args.iter().any(|a| a.contains("PrivateNetwork")));
    }

    #[test]
    fn systemd_run_args_drops_to_caller_identity() {
        let runner = Path::new(PORTAL_RUNNER_PATH);
        let mut r = req("http://captive.example/");
        r.caller_uid = 1234;
        let args = systemd_run_args(runner, &r);
        assert!(args.iter().any(|a| a == "--uid=1234"));
        assert!(args.iter().any(|a| a == "--gid=1234"));
    }

    #[test]
    fn systemd_run_args_terminates_options_before_command() {
        // The `--` guard means a portal URL shaped like an option (already
        // rejected by validate_portal_url, but defence in depth) cannot be
        // parsed as a systemd-run flag.
        let runner = Path::new(PORTAL_RUNNER_PATH);
        let args = systemd_run_args(runner, &req("http://captive.example/x"));
        let dashdash = args.iter().position(|a| a == "--").expect("`--` present");
        let runner_pos = args
            .iter()
            .position(|a| a == PORTAL_RUNNER_PATH)
            .expect("runner present");
        let url_pos = args
            .iter()
            .position(|a| a == "http://captive.example/x")
            .expect("url present");
        assert!(dashdash < runner_pos);
        assert!(runner_pos < url_pos);
    }

    // ── FakeSpawner behaviour ───────────────────────────────────────────

    #[test]
    fn fake_records_request_and_returns_unique_pid() {
        let s = FakeSpawner::new();
        let pid1 = s.spawn(&req("http://example.com/")).unwrap();
        let pid2 = s.spawn(&req("https://example.com/")).unwrap();
        assert_ne!(pid1, pid2);
        assert_eq!(s.requests().len(), 2);
        assert_eq!(s.requests()[0].portal_url, "http://example.com/");
    }

    #[test]
    fn fake_propagates_url_validation_failure() {
        let s = FakeSpawner::new();
        let err = s.spawn(&req("file:///etc/passwd")).unwrap_err();
        assert!(matches!(err, SpawnError::InvalidUrl(_)));
        assert_eq!(s.requests().len(), 0);
    }

    #[test]
    fn fake_can_force_syscall_failure() {
        let s = FakeSpawner::new();
        s.fail_for_url("http://bad.example/");
        let err = s.spawn(&req("http://bad.example/")).unwrap_err();
        assert!(matches!(err, SpawnError::SyscallFailed(_)));
    }

    #[test]
    fn fake_fires_exit_callback() {
        use std::sync::atomic::{AtomicU32, Ordering};
        let s = FakeSpawner::new();
        let pid = s.spawn(&req("http://example.com/")).unwrap();
        let observed_pid = Arc::new(AtomicU32::new(0));
        let observed_pid_clone = Arc::clone(&observed_pid);
        s.set_exit_callback(Some(Arc::new(move |exit: SpawnExit| {
            observed_pid_clone.store(exit.pid, Ordering::SeqCst);
        })));
        s.fire_exit(SpawnExit {
            pid,
            exit_code: Some(0),
            signal: None,
        });
        assert_eq!(observed_pid.load(Ordering::SeqCst), pid);
    }

    #[test]
    fn spawn_exit_is_clean_only_for_zero_exit() {
        assert!(
            SpawnExit {
                pid: 1,
                exit_code: Some(0),
                signal: None
            }
            .is_clean()
        );
        assert!(
            !SpawnExit {
                pid: 1,
                exit_code: Some(1),
                signal: None
            }
            .is_clean()
        );
        assert!(
            !SpawnExit {
                pid: 1,
                exit_code: None,
                signal: Some(9)
            }
            .is_clean()
        );
    }
}
