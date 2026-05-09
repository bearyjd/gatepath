//! Orchestration service for the helper.
//!
//! Pure-logic layer that the zbus binary instantiates. Glues these concerns
//! together:
//!
//!   1. Validate the interface name against [`crate::validation`] (refuses
//!      VPN/tunnel/loopback/ethernet interfaces — the security spec from PR #14).
//!   2. Confirm with NetworkManager that the interface IS currently captive
//!      via [`crate::network_manager::CaptiveStateChecker`]. Runs BEFORE
//!      PolicyKit so the user doesn't see an auth prompt for a non-captive
//!      interface (PR #20).
//!   3. Per-sender rate limit via [`crate::throttle::Throttle`] (PR #21).
//!      Closes the prompt-fatigue DoS that survives reorder: malicious caller
//!      spamming `SetupCaptive` on a real captive WiFi can still trigger one
//!      polkit prompt per call. Throttle caps that at N per window.
//!   4. PolicyKit auth check via [`crate::auth::Authorizer`].
//!   5. Drive the kernel ops via [`crate::netns::NetnsOps`].
//!   6. Track a single active session — a second concurrent setup is refused
//!      with [`crate::RefusalReason::AlreadyActive`].
//!   7. Watch the requesting sender on D-Bus via
//!      [`crate::name_watch::NameWatcher`] (Phase 5b.6). If the sender's
//!      connection drops without an explicit teardown, the helper auto-tears
//!      down the netns. Closes the leak window where the UI crashes mid-session.
//!   8. Audit-log every decision (success or refusal) via
//!      [`crate::audit_log::AuditWriter`].
//!
//! The order is deliberate. Validation runs FIRST so that even if any later
//! subsystem misbehaves, an attacker cannot reach privileged code paths
//! with a malicious interface name.

use std::sync::{Arc, Mutex, Weak};

use crate::{
    RefusalReason, SetupCaptiveRequest, SetupCaptiveResponse, TeardownCaptiveResponse,
    audit_log::{AuditAction, AuditDecision, AuditWriter, entry_now},
    auth::{ACTION_SETUP_CAPTIVE, ACTION_TEARDOWN_CAPTIVE, AuthError, Authorizer},
    name_watch::{NameWatcher, WatchGuard},
    netns::NetnsOps,
    network_manager::{CaptiveStateChecker, NMError},
    throttle::Throttle,
    validation::validate_interface_name,
};

/// Fixed netns name. Helper only ever manages one captive session at a time
/// (Gatepath only has one in flight), so a constant is sufficient.
pub const NETNS_NAME: &str = "gatepath";

pub struct GatepathHelperService<N: NetnsOps, A: Authorizer, C: CaptiveStateChecker, W: NameWatcher>
{
    ops: N,
    auth: A,
    captive_check: C,
    throttle: Throttle,
    watcher: W,
    audit: Box<dyn AuditWriter>,
    /// `Some(interface_name)` while a session is active, `None` otherwise.
    /// Mutex because the D-Bus service handles concurrent calls; this field
    /// is the lock that prevents two concurrent setups racing.
    active: Mutex<Option<String>>,
    /// Cancellation guard for the name watch installed during setup. Drop
    /// happens on explicit teardown (cancels the watch so the auto-teardown
    /// callback won't fire) and on auto-teardown (idempotent — by then the
    /// callback has already taken itself out).
    active_guard: Mutex<Option<WatchGuard>>,
}

impl<
    N: NetnsOps + Send + Sync + 'static,
    A: Authorizer + Send + Sync + 'static,
    C: CaptiveStateChecker + Send + Sync + 'static,
    W: NameWatcher,
> GatepathHelperService<N, A, C, W>
{
    pub fn new(
        ops: N,
        auth: A,
        captive_check: C,
        throttle: Throttle,
        watcher: W,
        audit: Box<dyn AuditWriter>,
    ) -> Self {
        Self {
            ops,
            auth,
            captive_check,
            throttle,
            watcher,
            audit,
            active: Mutex::new(None),
            active_guard: Mutex::new(None),
        }
    }

    /// Handle a `SetupCaptiveNetns` D-Bus call. Takes `&Arc<Self>` so the
    /// auto-teardown callback can hold a `Weak<Self>` and re-enter the
    /// service when the watched sender disconnects.
    pub fn setup_captive(
        self: &Arc<Self>,
        request: &SetupCaptiveRequest,
        sender: &str,
    ) -> SetupCaptiveResponse {
        let response = self.setup_captive_inner(request, sender);
        self.audit_setup(request, sender, &response);
        response
    }

    fn setup_captive_inner(
        self: &Arc<Self>,
        request: &SetupCaptiveRequest,
        sender: &str,
    ) -> SetupCaptiveResponse {
        // 1. Validation FIRST — the security boundary.
        if validate_interface_name(&request.interface_name).is_err() {
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::InvalidInterface,
            };
        }

        // 2. NetworkManager check BEFORE auth (PR #20 reorder).
        match self.captive_check.is_captive(&request.interface_name) {
            Ok(true) => {}
            Ok(false) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::NotCaptive,
                };
            }
            Err(NMError::Pending(_)) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::Pending,
                };
            }
            Err(NMError::InterfaceNotFound(_)) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::InvalidInterface,
                };
            }
            Err(NMError::DbusFailed(_)) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::BackendUnavailable,
                };
            }
        }

        // 3. Throttle: per-sender rate-limit before PolicyKit. A malicious
        //    caller spamming SetupCaptive on a real captive WiFi would
        //    otherwise trigger a polkit prompt for every call.
        if !self.throttle.allow(sender) {
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::Throttled,
            };
        }

        // 4. PolicyKit — only after the request is plausibly valid AND
        //    the sender hasn't blown through the rate limit.
        if let Err(err) = self.auth.check(ACTION_SETUP_CAPTIVE, sender) {
            return SetupCaptiveResponse::Refused {
                reason: refusal_for_auth_error(&err),
            };
        }

        // 5. Single-session lock — refuse a concurrent setup before touching
        //    the kernel.
        let mut active = self.active.lock().expect("active mutex poisoned");
        if active.is_some() {
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::AlreadyActive,
            };
        }

        // 6. Kernel ops. On failure, the session does NOT become active.
        let netns_path = match self.ops.create_netns(NETNS_NAME) {
            Ok(p) => p,
            Err(_) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::KernelError,
                };
            }
        };

        if let Err(err) = self.ops.move_interface(&request.interface_name, NETNS_NAME) {
            // Best-effort teardown so we don't leak a netns on partial failure.
            let _ = self.ops.destroy_netns(NETNS_NAME);
            tracing::error!(error = %err, "move_interface failed");
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            };
        }

        // 7. Install the name watch so we auto-teardown if the UI dies. If
        //    the watch can't be installed, undo the kernel ops and return
        //    KernelError — running without an auto-teardown would leak the
        //    netns past UI crashes, which 5b.6 exists to prevent.
        let weak: Weak<Self> = Arc::downgrade(self);
        let sender_for_cb = sender.to_string();
        let cb: Box<dyn FnOnce() + Send + 'static> = Box::new(move || {
            if let Some(strong) = weak.upgrade() {
                strong.handle_disconnect(&sender_for_cb);
            }
        });
        let guard = match self.watcher.watch(sender, cb) {
            Ok(g) => g,
            Err(err) => {
                tracing::error!(error = %err, "name watch install failed");
                let _ = self.ops.destroy_netns(NETNS_NAME);
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::KernelError,
                };
            }
        };

        *active = Some(request.interface_name.clone());
        *self.active_guard.lock().expect("guard mutex poisoned") = Some(guard);

        SetupCaptiveResponse::Success {
            netns_path: netns_path.to_string_lossy().into_owned(),
        }
    }

    /// Handle a `TeardownCaptiveNetns` D-Bus call.
    pub fn teardown_captive(&self, sender: &str) -> TeardownCaptiveResponse {
        let response = self.teardown_captive_inner(sender);
        self.audit_teardown(sender, &response);
        response
    }

    fn teardown_captive_inner(&self, sender: &str) -> TeardownCaptiveResponse {
        if let Err(err) = self.auth.check(ACTION_TEARDOWN_CAPTIVE, sender) {
            tracing::error!(error = %err, "teardown auth check failed");
            return TeardownCaptiveResponse::KernelError;
        }

        let mut active = self.active.lock().expect("active mutex poisoned");
        if active.is_none() {
            return TeardownCaptiveResponse::NotActive;
        }

        // Drop the watch guard BEFORE destroy so the auto-teardown callback
        // can't race in and fire after we've cleaned up. Drop happens
        // synchronously (the guard's cancel closure runs here).
        *self.active_guard.lock().expect("guard mutex poisoned") = None;

        match self.ops.destroy_netns(NETNS_NAME) {
            Ok(()) => {
                *active = None;
                let _ = sender; // sender used by audit; suppress unused-on-Ok
                TeardownCaptiveResponse::Success
            }
            Err(_) => TeardownCaptiveResponse::KernelError,
        }
    }

    /// Auto-teardown path. Fired by the name watcher when the requesting
    /// sender's D-Bus connection disconnects without an explicit teardown.
    /// No PolicyKit check — see module docs in [`crate::name_watch`] for the
    /// security argument. Idempotent: if an explicit teardown already
    /// cleared the session, this call is a no-op.
    fn handle_disconnect(&self, sender: &str) {
        let mut active = self.active.lock().expect("active mutex poisoned");
        if active.is_none() {
            return;
        }

        // Clear the guard now that we've started reacting; no further
        // signals matter. (The guard's cancel closure no-ops because the
        // callback we're running was already taken from cb_holder.)
        *self.active_guard.lock().expect("guard mutex poisoned") = None;

        let result = self.ops.destroy_netns(NETNS_NAME);
        *active = None;
        drop(active);

        let decision = match &result {
            Ok(()) => AuditDecision::Success,
            Err(err) => AuditDecision::Refused {
                reason: format!("kernel_error: {err}"),
            },
        };
        let entry = entry_now(AuditAction::AutoTeardown, sender, None, decision);
        self.audit.append(&entry);
    }

    fn audit_setup(
        &self,
        request: &SetupCaptiveRequest,
        sender: &str,
        response: &SetupCaptiveResponse,
    ) {
        let decision = match response {
            SetupCaptiveResponse::Success { .. } => AuditDecision::Success,
            SetupCaptiveResponse::Refused { reason } => AuditDecision::Refused {
                reason: reason.as_str().to_string(),
            },
        };
        let entry = entry_now(
            AuditAction::SetupCaptive,
            sender,
            Some(request.interface_name.clone()),
            decision,
        );
        self.audit.append(&entry);
    }

    fn audit_teardown(&self, sender: &str, response: &TeardownCaptiveResponse) {
        let decision = match response {
            TeardownCaptiveResponse::Success => AuditDecision::Success,
            TeardownCaptiveResponse::NotActive => AuditDecision::Refused {
                reason: "not_active".into(),
            },
            TeardownCaptiveResponse::KernelError => AuditDecision::Refused {
                reason: "kernel_error".into(),
            },
        };
        let entry = entry_now(AuditAction::TeardownCaptive, sender, None, decision);
        self.audit.append(&entry);
    }

    /// Test-only accessor. Lets tests verify state without exposing the
    /// mutex publicly in production code.
    #[cfg(test)]
    fn is_active(&self) -> bool {
        self.active.lock().unwrap().is_some()
    }
}

fn refusal_for_auth_error(err: &AuthError) -> RefusalReason {
    match err {
        AuthError::Denied { .. } => RefusalReason::Unauthorised,
        // Backend error — observable as denial from the user POV; never
        // auto-grant.
        AuthError::Error(_) => RefusalReason::KernelError,
    }
}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::audit_log::FakeAuditWriter;
    use crate::auth::FakeAuthorizer;
    use crate::name_watch::FakeNameWatcher;
    use crate::netns::{FakeNetnsOps, NetnsError};
    use crate::network_manager::FakeCaptiveCheck;
    use std::time::Duration;

    fn req(iface: &str) -> SetupCaptiveRequest {
        SetupCaptiveRequest {
            interface_name: iface.into(),
        }
    }

    fn allow_captive() -> FakeCaptiveCheck {
        let nm = FakeCaptiveCheck::new();
        nm.say_captive("wlan0");
        nm.say_captive("wlp3s0");
        nm
    }

    fn permissive_throttle() -> Throttle {
        Throttle::new(1_000_000, Duration::from_secs(60))
    }

    type TestSvc =
        GatepathHelperService<FakeNetnsOps, FakeAuthorizer, FakeCaptiveCheck, Arc<FakeNameWatcher>>;

    fn svc_with_audit(
        ops: FakeNetnsOps,
        auth: FakeAuthorizer,
        nm: FakeCaptiveCheck,
        throttle: Throttle,
    ) -> (Arc<TestSvc>, Arc<FakeAuditWriter>, Arc<FakeNameWatcher>) {
        let audit = Arc::new(FakeAuditWriter::new());
        let watcher = Arc::new(FakeNameWatcher::new());
        let audit_ref = Arc::clone(&audit);
        let watcher_ref = Arc::clone(&watcher);
        struct ArcWriter(Arc<FakeAuditWriter>);
        impl AuditWriter for ArcWriter {
            fn append(&self, entry: &crate::audit_log::AuditEntry) {
                self.0.append(entry);
            }
        }
        let svc = Arc::new(GatepathHelperService::new(
            ops,
            auth,
            nm,
            throttle,
            watcher_ref,
            Box::new(ArcWriter(audit_ref)),
        ));
        (svc, audit, watcher)
    }

    #[test]
    fn setup_with_valid_input_succeeds_and_marks_active() {
        let (svc, audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(matches!(resp, SetupCaptiveResponse::Success { .. }));
        assert!(svc.is_active());
        assert_eq!(audit.entries().len(), 1);
        assert_eq!(audit.entries()[0].decision, AuditDecision::Success);
    }

    #[test]
    fn setup_with_invalid_interface_skips_auth_and_kernel() {
        let (svc, audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("tun0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::InvalidInterface,
            },
        );
        assert_eq!(svc.auth.checks().len(), 0);
        assert!(!svc.is_active());
        assert_eq!(audit.entries().len(), 1);
        assert!(matches!(
            audit.entries()[0].decision,
            AuditDecision::Refused { .. }
        ));
    }

    #[test]
    fn setup_with_auth_denied_does_not_touch_kernel() {
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::deny_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Unauthorised,
            },
        );
        assert!(svc.ops.netns().is_empty());
        assert!(!svc.is_active());
    }

    #[test]
    fn setup_with_auth_backend_error_returns_kernel_error() {
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::errored("polkit unreachable"),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
    }

    #[test]
    fn setup_with_non_captive_interface_returns_not_captive() {
        let nm = FakeCaptiveCheck::new();
        nm.say_not_captive("wlan0");
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::NotCaptive,
            },
        );
        assert!(!svc.is_active());
    }

    #[test]
    fn setup_with_nm_unreachable_returns_backend_unavailable() {
        let nm = FakeCaptiveCheck::new();
        nm.fail_dbus();
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::BackendUnavailable,
            },
        );
    }

    #[test]
    fn setup_with_pending_nm_state_returns_pending() {
        let nm = FakeCaptiveCheck::new();
        nm.say_pending("wlan0");
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Pending,
            },
        );
        assert_eq!(svc.auth.checks().len(), 0);
    }

    #[test]
    fn setup_with_unknown_interface_returns_invalid_interface() {
        let nm = FakeCaptiveCheck::new();
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::InvalidInterface,
            },
        );
    }

    #[test]
    fn second_setup_returns_already_active() {
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let _first = svc.setup_captive(&req("wlan0"), ":1.42");
        let second = svc.setup_captive(&req("wlp3s0"), ":1.42");
        assert_eq!(
            second,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::AlreadyActive,
            },
        );
    }

    #[test]
    fn teardown_when_active_succeeds_and_clears_state() {
        let (svc, audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(svc.is_active());
        let resp = svc.teardown_captive(":1.42");
        assert_eq!(resp, TeardownCaptiveResponse::Success);
        assert!(!svc.is_active());
        assert_eq!(audit.entries().len(), 2);
        assert_eq!(audit.entries()[1].action, AuditAction::TeardownCaptive);
        assert_eq!(audit.entries()[1].decision, AuditDecision::Success);
    }

    #[test]
    fn teardown_when_idle_returns_not_active() {
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.teardown_captive(":1.42");
        assert_eq!(resp, TeardownCaptiveResponse::NotActive);
    }

    #[test]
    fn teardown_with_auth_denied_does_not_clear_state() {
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let before = svc.auth.checks().len();
        let _ = svc.teardown_captive(":1.42");
        assert_eq!(svc.auth.checks().len(), before + 1);
    }

    #[test]
    fn kernel_error_during_move_rolls_back_netns() {
        struct ExplodingMoveFake {
            inner: FakeNetnsOps,
        }
        impl NetnsOps for ExplodingMoveFake {
            fn create_netns(&self, name: &str) -> Result<std::path::PathBuf, NetnsError> {
                self.inner.create_netns(name)
            }
            fn move_interface(&self, _i: &str, _n: &str) -> Result<(), NetnsError> {
                Err(NetnsError::MoveFailed {
                    interface: "wlan0".into(),
                    netns: "gatepath".into(),
                    stderr: "EBUSY (simulated)".into(),
                })
            }
            fn destroy_netns(&self, name: &str) -> Result<(), NetnsError> {
                self.inner.destroy_netns(name)
            }
        }
        let exploding = ExplodingMoveFake {
            inner: FakeNetnsOps::new(),
        };
        let svc = Arc::new(GatepathHelperService::new(
            exploding,
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
            Arc::new(FakeNameWatcher::new()),
            Box::new(FakeAuditWriter::new()),
        ));
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
        assert!(!svc.is_active());
    }

    // ── Phase 5b.5 throttle + audit ──────────────────────────────────────

    #[test]
    fn setup_throttled_after_burst_returns_throttled() {
        let throttle = Throttle::new(2, Duration::from_secs(60));
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            throttle,
        );
        let _first = svc.setup_captive(&req("wlan0"), ":1.42");
        let _second = svc.setup_captive(&req("wlan0"), ":1.42");
        let third = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            third,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Throttled,
            },
        );
    }

    #[test]
    fn throttled_setup_does_not_consume_auth_check() {
        let throttle = Throttle::new(1, Duration::from_secs(60));
        let (svc, _audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            throttle,
        );
        let _first = svc.setup_captive(&req("wlan0"), ":1.42");
        let auth_count_after_first = svc.auth.checks().len();
        let throttled = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            throttled,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Throttled,
            },
        );
        assert_eq!(svc.auth.checks().len(), auth_count_after_first);
    }

    #[test]
    fn audit_records_refusal_with_reason_string() {
        let nm = FakeCaptiveCheck::new();
        nm.say_not_captive("wlan0");
        let (svc, audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let _resp = svc.setup_captive(&req("wlan0"), ":1.42");
        let entries = audit.entries();
        assert_eq!(entries.len(), 1);
        match &entries[0].decision {
            AuditDecision::Refused { reason } => assert_eq!(reason, "not_captive"),
            other => panic!("expected Refused, got {other:?}"),
        }
    }

    #[test]
    fn audit_records_setup_then_teardown_in_order() {
        let (svc, audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        svc.teardown_captive(":1.42");
        let entries = audit.entries();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].action, AuditAction::SetupCaptive);
        assert_eq!(entries[0].interface, Some("wlan0".to_string()));
        assert_eq!(entries[1].action, AuditAction::TeardownCaptive);
        assert_eq!(entries[1].interface, None);
    }

    #[test]
    fn throttled_setup_still_writes_audit_entry() {
        let throttle = Throttle::new(1, Duration::from_secs(60));
        let (svc, audit, _w) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            throttle,
        );
        let _first = svc.setup_captive(&req("wlan0"), ":1.42");
        let _second_throttled = svc.setup_captive(&req("wlan0"), ":1.42");
        let entries = audit.entries();
        assert_eq!(entries.len(), 2);
        match &entries[1].decision {
            AuditDecision::Refused { reason } => assert_eq!(reason, "throttled"),
            other => panic!("expected throttled refusal, got {other:?}"),
        }
    }

    // ── Phase 5b.6 NEW tests: name watch + auto-teardown ─────────────────

    #[test]
    fn setup_installs_watch_on_active_sender() {
        let (svc, _audit, watcher) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(watcher.is_watching(":1.42"));
    }

    #[test]
    fn refused_setup_does_not_install_watch() {
        let nm = FakeCaptiveCheck::new();
        nm.say_not_captive("wlan0");
        let (svc, _audit, watcher) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(watcher.watched_count(), 0);
    }

    #[test]
    fn disconnect_fires_auto_teardown() {
        let (svc, audit, watcher) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(svc.is_active());
        watcher.fire_disconnect(":1.42");
        assert!(!svc.is_active());
        // setup audit + auto-teardown audit
        let entries = audit.entries();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[1].action, AuditAction::AutoTeardown);
        assert_eq!(entries[1].sender, ":1.42");
        assert_eq!(entries[1].decision, AuditDecision::Success);
    }

    #[test]
    fn explicit_teardown_cancels_watch() {
        let (svc, _audit, watcher) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        svc.teardown_captive(":1.42");
        assert!(!watcher.is_watching(":1.42"));
        // Firing now is a no-op — watch is gone.
        watcher.fire_disconnect(":1.42");
        // Still not active; no double-teardown.
        assert!(!svc.is_active());
    }

    #[test]
    fn watch_install_failure_rolls_back_kernel_and_returns_kernel_error() {
        let watcher = Arc::new(FakeNameWatcher::new());
        watcher.fail_for(":1.42");
        let audit = Arc::new(FakeAuditWriter::new());
        let audit_ref = Arc::clone(&audit);
        struct ArcWriter(Arc<FakeAuditWriter>);
        impl AuditWriter for ArcWriter {
            fn append(&self, entry: &crate::audit_log::AuditEntry) {
                self.0.append(entry);
            }
        }
        let svc = Arc::new(GatepathHelperService::new(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
            Arc::clone(&watcher),
            Box::new(ArcWriter(audit_ref)),
        ));
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
        assert!(!svc.is_active());
        // Netns must be destroyed — leaking a netns here would defeat 5b.6.
        assert!(svc.ops.netns().is_empty());
    }

    #[test]
    fn setup_after_disconnect_succeeds() {
        // After a disconnect-driven teardown, the helper should accept a
        // fresh setup. Pins that handle_disconnect actually clears state.
        let (svc, _audit, watcher) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        watcher.fire_disconnect(":1.42");
        let second = svc.setup_captive(&req("wlan0"), ":1.99");
        assert!(matches!(second, SetupCaptiveResponse::Success { .. }));
        assert!(watcher.is_watching(":1.99"));
    }
}
