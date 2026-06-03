//! zbus binding for [`GatepathHelperService`].
//!
//! Translates between the Rust orchestration layer (sync, generic over
//! `NetnsOps` + `Authorizer`) and the wire protocol (async, monomorphic on
//! the production types). Two D-Bus methods:
//!
//! - `SetupCaptive(interface_name: s) -> s` — returns netns path on success,
//!   D-Bus error on refusal. Refusal reasons map to distinct error names so
//!   the client can show specific user feedback.
//! - `TeardownCaptive() -> ()` — returns nothing on success, error on
//!   refusal. `NotActive` is also an error so callers don't silently
//!   succeed against a fresh helper.
//!
//! The orchestration logic in [`crate::service::GatepathHelperService`] is
//! sync; method handlers wrap calls in `spawn_blocking` so blocking syscalls
//! (running `ip`, calling `pkcheck`-equivalent over D-Bus) don't stall the
//! tokio reactor.

use std::sync::Arc;

use crate::auth::Authorizer;
use crate::name_watch::NameWatcher;
use crate::netns::NetnsOps;
use crate::network_manager::CaptiveStateChecker;
use crate::service::GatepathHelperService;
use crate::{
    LaunchPortalRequest, LaunchPortalResponse, RefusalReason, SetupCaptiveRequest,
    SetupCaptiveResponse, TeardownCaptiveResponse,
};

/// Stable D-Bus interface name. Bumping the `1` is the protocol-version
/// signal; clients pinned to v1 keep working until we ship a v2 that
/// coexists.
pub const INTERFACE: &str = "cc.grepon.Gatepath.NetNsHelper1";
pub const OBJECT_PATH: &str = "/cc/grepon/Gatepath/NetNsHelper";
pub const BUS_NAME: &str = "cc.grepon.Gatepath.NetNsHelper";

/// D-Bus error type for the helper. Maps each [`RefusalReason`] to a
/// distinct error name so clients can branch on it.
#[derive(Debug, zbus::DBusError)]
#[zbus(prefix = "cc.grepon.Gatepath.NetNsHelper.Error")]
pub enum HelperError {
    #[zbus(error)]
    ZBus(zbus::Error),
    /// Interface name failed `validate_interface_name` OR doesn't exist on
    /// the system per `NetworkManager`'s device list.
    InvalidInterface(String),
    /// Interface exists but `NetworkManager` does not flag it as captive.
    NotCaptive(String),
    /// `NetworkManager` is still evaluating connectivity. UI should retry.
    Pending(String),
    /// `PolicyKit` denied authorisation for the calling user.
    Unauthorised(String),
    /// `NetworkManager` D-Bus service is unreachable. Distinct from
    /// `KernelError` so the UI can suggest "is NetworkManager running?".
    BackendUnavailable(String),
    /// Kernel returned an error during the netns/interface migration.
    KernelError(String),
    /// A previous setup is still active and hasn't been torn down.
    AlreadyActive(String),
    /// Teardown called when no session is active.
    NotActive(String),
    /// Caller has exceeded the per-sender rate limit. UI should back off
    /// without prompting the user again.
    Throttled(String),
    /// Phase 5b.7: portal URL failed validation (scheme, control byte, etc.).
    InvalidPortalUrl(String),
    /// Phase 5b.7: `LaunchPortal` called without a prior `SetupCaptive`.
    NoActiveSession(String),
    /// Phase 5b.7: caller is not the session owner.
    SenderMismatch(String),
    /// Phase 5b.7 / DESK-003 C4: launching the WebView's transient `systemd-run` unit failed.
    SpawnFailed(String),
    /// DESK-002: the captive network is secured; only open networks can be
    /// re-associated inside the netns today.
    UnsupportedSecurity(String),
}

/// Extract the sender's bus name from the message header, refusing the call
/// up-front with `Unauthorised` if it's missing.
///
/// `header.sender()` returns `None` for peer-to-peer connections or before a
/// connection has finished its initial handshake. Both are unusual but real;
/// previously we mapped this to an empty string passed to PolicyKit, which
/// returns an error and surfaces as `KernelError`. That misclassifies an
/// auth condition as an internal error. Refuse explicitly.
fn sender_or_unauthorised(header: &zbus::message::Header<'_>) -> Result<String, HelperError> {
    match header.sender() {
        Some(name) => Ok(name.to_string()),
        None => Err(HelperError::Unauthorised(
            "no sender on D-Bus header (peer-to-peer connection?)".into(),
        )),
    }
}

impl HelperError {
    fn from_refusal(reason: RefusalReason) -> Self {
        match reason {
            RefusalReason::InvalidInterface => {
                Self::InvalidInterface("interface name not usable".into())
            }
            RefusalReason::NotCaptive => Self::NotCaptive("not flagged captive".into()),
            RefusalReason::Pending => Self::Pending("NetworkManager still evaluating".into()),
            RefusalReason::Unauthorised => Self::Unauthorised("PolicyKit denied".into()),
            RefusalReason::BackendUnavailable => {
                Self::BackendUnavailable("NetworkManager unreachable".into())
            }
            RefusalReason::KernelError => Self::KernelError("kernel op failed".into()),
            RefusalReason::AlreadyActive => Self::AlreadyActive("session in flight".into()),
            RefusalReason::Throttled => {
                Self::Throttled("rate limit exceeded for this sender".into())
            }
            RefusalReason::InvalidPortalUrl => {
                Self::InvalidPortalUrl("portal URL failed validation".into())
            }
            RefusalReason::NoActiveSession => {
                Self::NoActiveSession("no captive session active".into())
            }
            RefusalReason::SenderMismatch => {
                Self::SenderMismatch("caller is not the session owner".into())
            }
            RefusalReason::SpawnFailed => Self::SpawnFailed("subprocess spawn failed".into()),
            RefusalReason::UnsupportedSecurity => {
                Self::UnsupportedSecurity("secured captive networks are not supported".into())
            }
        }
    }
}

/// zbus-facing wrapper over `GatepathHelperService`. Generic over the same
/// trait params so production wiring (`LinuxNetnsOps`, `PolicyKitAuthorizer`,
/// `NMCaptiveCheck`, `LinuxNameWatcher`) and integration tests (with fakes)
/// share the binding.
pub struct DbusService<
    N: NetnsOps + Send + Sync + 'static,
    A: Authorizer + Send + Sync + 'static,
    C: CaptiveStateChecker + Send + Sync + 'static,
    W: NameWatcher,
> {
    inner: Arc<GatepathHelperService<N, A, C, W>>,
}

impl<
    N: NetnsOps + Send + Sync + 'static,
    A: Authorizer + Send + Sync + 'static,
    C: CaptiveStateChecker + Send + Sync + 'static,
    W: NameWatcher,
> DbusService<N, A, C, W>
{
    pub fn new(inner: Arc<GatepathHelperService<N, A, C, W>>) -> Self {
        Self { inner }
    }
}

#[zbus::interface(name = "cc.grepon.Gatepath.NetNsHelper1")]
impl<
    N: NetnsOps + Send + Sync + 'static,
    A: Authorizer + Send + Sync + 'static,
    C: CaptiveStateChecker + Send + Sync + 'static,
    W: NameWatcher,
> DbusService<N, A, C, W>
{
    /// `SetupCaptive(interface_name: s) -> s`
    ///
    /// Returns the netns path (`/var/run/netns/gatepath`) on success, a
    /// typed D-Bus error on refusal.
    async fn setup_captive(
        &self,
        interface_name: String,
        #[zbus(header)] header: zbus::message::Header<'_>,
    ) -> Result<String, HelperError> {
        let sender = sender_or_unauthorised(&header)?;
        let request = SetupCaptiveRequest { interface_name };
        let inner = Arc::clone(&self.inner);
        let response = tokio::task::spawn_blocking(move || inner.setup_captive(&request, &sender))
            .await
            .map_err(|e| HelperError::KernelError(format!("join error: {e}")))?;
        match response {
            SetupCaptiveResponse::Success { netns_path } => Ok(netns_path),
            SetupCaptiveResponse::Refused { reason } => Err(HelperError::from_refusal(reason)),
        }
    }

    /// `TeardownCaptive() -> ()`
    async fn teardown_captive(
        &self,
        #[zbus(header)] header: zbus::message::Header<'_>,
    ) -> Result<(), HelperError> {
        let sender = sender_or_unauthorised(&header)?;
        let inner = Arc::clone(&self.inner);
        let response = tokio::task::spawn_blocking(move || inner.teardown_captive(&sender))
            .await
            .map_err(|e| HelperError::KernelError(format!("join error: {e}")))?;
        match response {
            TeardownCaptiveResponse::Success => Ok(()),
            TeardownCaptiveResponse::NotActive => {
                Err(HelperError::NotActive("nothing to tear down".into()))
            }
            TeardownCaptiveResponse::KernelError => {
                Err(HelperError::KernelError("teardown failed".into()))
            }
        }
    }

    /// `LaunchPortal(portal_url: s) -> u`  (Phase 5b.7)
    ///
    /// Returns the spawned subprocess's PID on success, a typed D-Bus
    /// error on refusal. The subprocess runs inside the gatepath netns
    /// as the calling user. Helper independently observes the subprocess
    /// exit and emits [`Self::portal_subprocess_exited`] as a signal.
    async fn launch_portal(
        &self,
        portal_url: String,
        #[zbus(header)] header: zbus::message::Header<'_>,
    ) -> Result<u32, HelperError> {
        let sender = sender_or_unauthorised(&header)?;
        let request = LaunchPortalRequest { portal_url };
        let inner = Arc::clone(&self.inner);
        let response =
            tokio::task::spawn_blocking(move || inner.launch_portal_subprocess(&request, &sender))
                .await
                .map_err(|e| HelperError::KernelError(format!("join error: {e}")))?;
        match response {
            LaunchPortalResponse::Success { pid } => Ok(pid),
            LaunchPortalResponse::Refused { reason } => Err(HelperError::from_refusal(reason)),
        }
    }

    /// `PortalSubprocessExited(pid: u, exit_code: i, signal_num: i)`
    ///
    /// Emitted by the helper when a previously-spawned portal subprocess
    /// exits. `exit_code` is the normal exit code (or `-1` if signalled).
    /// `signal_num` is the signal number (or `0` if exited normally).
    /// Orchestrators subscribe to this signal to know when to call
    /// `TeardownCaptive`.
    #[zbus(signal)]
    pub async fn portal_subprocess_exited(
        signal_emitter: &zbus::object_server::SignalEmitter<'_>,
        pid: u32,
        exit_code: i32,
        signal_num: i32,
    ) -> zbus::Result<()>;
}
