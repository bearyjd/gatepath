//! Privileged subprocess spawn into the gatepath netns (Phase 5b.7).
//!
//! `setns(2)` to a root-owned netns requires `CAP_SYS_ADMIN` in BOTH the
//! caller's user namespace AND the netns's owning user namespace. The
//! Gatepath UI runs unprivileged; the kernel rejects setns from there. The
//! helper, which already holds CAP_NET_ADMIN, performs the namespace entry
//! on the caller's behalf, drops privilege to the caller's UID, and exec's
//! a hardcoded subprocess path — the WebView runner script bundled at
//! `/usr/lib/gatepath/portal-webview-runner`.
//!
//! ## Trust surface mitigations
//!
//! The spawn capability is the largest expansion of helper privilege so
//! far. To keep blast radius bounded:
//!
//! - **Hardcoded exec path** ([`PORTAL_RUNNER_PATH`]). No caller input
//!   reaches `execv`'s path argument. Even a compromised caller cannot
//!   redirect to an arbitrary binary.
//! - **One controlled argument**: the portal URL, validated against
//!   [`url::Url`] (RFC 3986) before reaching the subprocess. Rejected
//!   schemes other than `http`/`https`. Rejected control bytes
//!   (`< 0x20` or `== 0x7F`).
//! - **Drop privilege before exec**: `setresuid(uid, uid, uid)` followed
//!   by `setresgid` — the spawned process is fully unprivileged with the
//!   caller's identity. It can't `setuid` back to root.
//! - **Fail-closed**: any error during fork/setns/exec aborts the spawn
//!   entirely; no partial state. Audit log records the failure.
//!
//! ## Test strategy
//!
//! `LinuxSpawner` exercises real syscalls and requires root + a netns
//! mount; it is integration-tested under `--ignored` in
//! `tests/dbus_integration.rs`. Service-level tests use [`FakeSpawner`].

#![allow(unsafe_code)]
// SAFETY rationale (whole-module): `fork(2)` and `_exit(2)` are not safe
// abstractions wrapped by `nix`. `fork` is unsafe because the post-fork
// child shares mappings with the parent and must avoid any function that
// is not async-signal-safe. `_exit` is unsafe because it's a raw libc
// call. We use them only in the documented post-fork-pre-exec window:
// after fork, the child performs setns + setresuid + execv (all
// async-signal-safe per POSIX). On error in that window we MUST use
// `_exit` rather than `std::process::exit` (which runs destructors and
// is not async-signal-safe — calling it after fork in a multi-threaded
// program would be unsound, since other threads' locks held at fork
// time still appear locked in the child's copy of memory).

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use thiserror::Error;

/// Hardcoded exec path for the portal WebView runner. The helper refuses
/// to start if this file is missing — installers must place the runner
/// here regardless of how they ship the rest of the app. Flatpak users
/// do not get isolation; the system path is set up by the
/// distro-packaged helper, not by the Flatpak.
pub const PORTAL_RUNNER_PATH: &str = "/usr/lib/gatepath/portal-webview-runner";

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
    #[error("fork/setns/exec failed: {0}")]
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

/// Privileged spawn surface. Real impl ([`LinuxSpawner`]) performs
/// fork/setns/setresuid/exec; tests use [`FakeSpawner`].
pub trait Spawner: Send + Sync + 'static {
    /// Validate the request, fork into the netns, drop privilege, and
    /// exec the runner. Returns the child PID on success.
    ///
    /// Implementations MUST:
    /// 1. Re-validate the portal URL (the trait is the privileged surface;
    ///    don't trust prior validation).
    /// 2. Open the netns path BEFORE fork (so failure to open errors out
    ///    cleanly without leaving orphan state).
    /// 3. setns + setresuid in the child BEFORE execv.
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

use std::ffi::CString;
use std::path::Path;
use std::thread;

use nix::sched::{CloneFlags, setns};
use nix::sys::wait::{WaitStatus, waitpid};
use nix::unistd::{ForkResult, Gid, Uid, execv, fork, setresgid, setresuid};

/// Production spawner. Holds the runner path so tests can substitute a
/// different binary; production wiring uses [`PORTAL_RUNNER_PATH`].
pub struct LinuxSpawner {
    runner_path: PathBuf,
    exit_callback: Mutex<Option<ExitCallback>>,
}

impl LinuxSpawner {
    pub fn new(runner_path: impl Into<PathBuf>) -> Self {
        Self {
            runner_path: runner_path.into(),
            exit_callback: Mutex::new(None),
        }
    }
}

impl Spawner for LinuxSpawner {
    fn spawn(&self, request: &SpawnRequest) -> Result<u32, SpawnError> {
        validate_portal_url(&request.portal_url)?;

        // Open the netns fd in the parent BEFORE fork, so a missing netns
        // errors out cleanly without leaving an orphan child.
        let netns_path = netns_mount_path(&request.netns_name);
        let netns_file = std::fs::File::open(&netns_path).map_err(|e| {
            SpawnError::NetnsMissing {
                name: request.netns_name.clone(),
                path: netns_path.clone(),
            }
            .or_io(e)
        })?;

        let runner_c = CString::new(self.runner_path.as_os_str().to_string_lossy().as_bytes())
            .map_err(|e| SpawnError::SyscallFailed(format!("runner path NUL: {e}")))?;
        let url_c = CString::new(request.portal_url.as_bytes())
            .map_err(|e| SpawnError::SyscallFailed(format!("url NUL: {e}")))?;
        let argv = [runner_c.clone(), url_c];

        let target_uid = Uid::from_raw(request.caller_uid);
        let target_gid = Gid::from_raw(request.caller_uid); // primary GID == UID; safe assumption for desktop users

        // SAFETY: `fork` is the only async-signal-unsafe call in the
        // post-fork child path. Nothing else allocates between fork and
        // exec; the CStrings, path buffers, and fd are all stack/heap
        // pre-fork. `setns`/`setresuid`/`setresgid`/`execv` are
        // async-signal-safe.
        let pid = match unsafe { fork() } {
            Ok(ForkResult::Parent { child }) => child.as_raw() as u32,
            Ok(ForkResult::Child) => {
                // From here on, anything we touch must be async-signal-safe.
                // We do NOT use the stdlib's logging; we do NOT allocate.
                // On any error we _exit() with a distinct code so the
                // parent can audit the failure mode.
                if setns(netns_file, CloneFlags::CLONE_NEWNET).is_err() {
                    unsafe { libc::_exit(91) };
                }
                if setresgid(target_gid, target_gid, target_gid).is_err() {
                    unsafe { libc::_exit(92) };
                }
                if setresuid(target_uid, target_uid, target_uid).is_err() {
                    unsafe { libc::_exit(93) };
                }
                let _ = execv(&runner_c, &argv);
                // execv only returns on failure.
                unsafe { libc::_exit(94) };
            }
            Err(e) => {
                return Err(SpawnError::SyscallFailed(format!("fork failed: {e}")));
            }
        };

        // Spawn a thread to reap the child and notify the callback.
        let cb_holder = self.exit_callback.lock().unwrap().clone();
        thread::spawn(move || {
            let pid_arg = nix::unistd::Pid::from_raw(pid as i32);
            let exit = match waitpid(pid_arg, None) {
                Ok(WaitStatus::Exited(_, code)) => SpawnExit {
                    pid,
                    exit_code: Some(code),
                    signal: None,
                },
                Ok(WaitStatus::Signaled(_, sig, _)) => SpawnExit {
                    pid,
                    exit_code: None,
                    signal: Some(sig as i32),
                },
                Ok(other) => {
                    tracing::warn!(?other, "unexpected waitpid status");
                    SpawnExit {
                        pid,
                        exit_code: None,
                        signal: None,
                    }
                }
                Err(e) => {
                    tracing::error!(error = %e, "waitpid failed");
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

impl SpawnError {
    fn or_io(self, io: std::io::Error) -> Self {
        match self {
            Self::NetnsMissing { name, path } => Self::NetnsMissing {
                name,
                path: path.with_extension(format!("err:{io}")),
            },
            other => other,
        }
    }
}

fn netns_mount_path(name: &str) -> PathBuf {
    Path::new(crate::netns::NETNS_DIR).join(name)
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
