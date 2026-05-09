//! Orchestration service for the helper.
//!
//! Pure-logic layer that the zbus binary instantiates. Glues seven concerns
//! together:
//!
//!   1. Validate the interface name against [`crate::validation`] (refuses
//!      VPN/tunnel/loopback/ethernet interfaces — the security spec from PR #14).
//!   2. Confirm with NetworkManager that the interface IS currently captive
//!      via [`crate::network_manager::CaptiveStateChecker`]. Runs BEFORE
//!      PolicyKit so the user doesn't see an auth prompt for a non-captive
//!      interface (PR #20).
//!   3. Per-sender rate limit via [`crate::throttle::Throttle`] (PR after 5b.5).
//!      Closes the prompt-fatigue DoS that survives reorder: malicious caller
//!      spamming `SetupCaptive` on a real captive WiFi can still trigger one
//!      polkit prompt per call. Throttle caps that at N per window.
//!   4. PolicyKit auth check via [`crate::auth::Authorizer`].
//!   5. Drive the kernel ops via [`crate::netns::NetnsOps`].
//!   6. Track a single active session — a second concurrent setup is refused
//!      with [`crate::RefusalReason::AlreadyActive`].
//!   7. Audit-log every decision (success or refusal) via
//!      [`crate::audit_log::AuditWriter`].
//!
//! The order is deliberate. Validation runs FIRST so that even if any later
//! subsystem misbehaves, an attacker cannot reach privileged code paths
//! with a malicious interface name.

use std::sync::Mutex;

use crate::{
    RefusalReason, SetupCaptiveRequest, SetupCaptiveResponse, TeardownCaptiveResponse,
    audit_log::{AuditAction, AuditDecision, AuditWriter, entry_now},
    auth::{ACTION_SETUP_CAPTIVE, ACTION_TEARDOWN_CAPTIVE, AuthError, Authorizer},
    netns::NetnsOps,
    network_manager::{CaptiveStateChecker, NMError},
    throttle::Throttle,
    validation::validate_interface_name,
};

/// Fixed netns name. Helper only ever manages one captive session at a time
/// (Gatepath only has one in flight), so a constant is sufficient.
pub const NETNS_NAME: &str = "gatepath";

pub struct GatepathHelperService<N: NetnsOps, A: Authorizer, C: CaptiveStateChecker> {
    ops: N,
    auth: A,
    captive_check: C,
    throttle: Throttle,
    audit: Box<dyn AuditWriter>,
    /// `Some(interface_name)` while a session is active, `None` otherwise.
    /// Mutex because the D-Bus service handles concurrent calls; this field
    /// is the lock that prevents two concurrent setups racing.
    active: Mutex<Option<String>>,
}

impl<N: NetnsOps, A: Authorizer, C: CaptiveStateChecker> GatepathHelperService<N, A, C> {
    pub fn new(
        ops: N,
        auth: A,
        captive_check: C,
        throttle: Throttle,
        audit: Box<dyn AuditWriter>,
    ) -> Self {
        Self {
            ops,
            auth,
            captive_check,
            throttle,
            audit,
            active: Mutex::new(None),
        }
    }

    /// Handle a `SetupCaptiveNetns` D-Bus call.
    pub fn setup_captive(
        &self,
        request: &SetupCaptiveRequest,
        sender: &str,
    ) -> SetupCaptiveResponse {
        let response = self.setup_captive_inner(request, sender);
        self.audit_setup(request, sender, &response);
        response
    }

    fn setup_captive_inner(
        &self,
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
        //    otherwise trigger a polkit prompt for every call. Throttle
        //    caps that at the configured limit per window.
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

        // 6. Kernel ops. On failure, the session does NOT become active —
        //    we return KernelError and the caller can retry.
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
            tracing_error_msg(&err);
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            };
        }

        *active = Some(request.interface_name.clone());

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
            tracing_error_msg(&err);
            return TeardownCaptiveResponse::KernelError;
        }

        let mut active = self.active.lock().expect("active mutex poisoned");
        if active.is_none() {
            return TeardownCaptiveResponse::NotActive;
        }

        match self.ops.destroy_netns(NETNS_NAME) {
            Ok(()) => {
                *active = None;
                TeardownCaptiveResponse::Success
            }
            Err(_) => TeardownCaptiveResponse::KernelError,
        }
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
                reason: refusal_reason_name(*reason).to_string(),
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

/// Stable string names for [`RefusalReason`] variants. Used in the audit
/// log so the JSON is human-readable without needing crate internals.
fn refusal_reason_name(reason: RefusalReason) -> &'static str {
    match reason {
        RefusalReason::InvalidInterface => "invalid_interface",
        RefusalReason::NotCaptive => "not_captive",
        RefusalReason::Pending => "pending",
        RefusalReason::Unauthorised => "unauthorised",
        RefusalReason::BackendUnavailable => "backend_unavailable",
        RefusalReason::KernelError => "kernel_error",
        RefusalReason::AlreadyActive => "already_active",
        RefusalReason::Throttled => "throttled",
    }
}

/// Stub for future tracing wiring. Intentionally a no-op so we don't pull
/// in the `tracing` crate during 5b.2 — 5b.3 wires it up alongside the
/// audit log writer.
fn tracing_error_msg<T: std::fmt::Display>(_err: &T) {}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::audit_log::FakeAuditWriter;
    use crate::auth::FakeAuthorizer;
    use crate::netns::{FakeNetnsOps, NetnsError};
    use crate::network_manager::FakeCaptiveCheck;
    use std::time::Duration;

    fn req(iface: &str) -> SetupCaptiveRequest {
        SetupCaptiveRequest {
            interface_name: iface.into(),
        }
    }

    /// Permissive captive checker — says wlan*-named interfaces are captive.
    /// Used by tests not specifically exercising the captive gate.
    fn allow_captive() -> FakeCaptiveCheck {
        let nm = FakeCaptiveCheck::new();
        nm.say_captive("wlan0");
        nm.say_captive("wlp3s0");
        nm
    }

    /// Effectively-unlimited throttle for tests not exercising rate limiting.
    fn permissive_throttle() -> Throttle {
        Throttle::new(1_000_000, Duration::from_secs(60))
    }

    /// Build a service with a fresh FakeAuditWriter exposed for assertion.
    fn svc_with_audit(
        ops: FakeNetnsOps,
        auth: FakeAuthorizer,
        nm: FakeCaptiveCheck,
        throttle: Throttle,
    ) -> (
        GatepathHelperService<FakeNetnsOps, FakeAuthorizer, FakeCaptiveCheck>,
        std::sync::Arc<FakeAuditWriter>,
    ) {
        let audit = std::sync::Arc::new(FakeAuditWriter::new());
        let audit_ref = std::sync::Arc::clone(&audit);
        // We need to put the FakeAuditWriter behind the trait object AND
        // keep an Arc handle for tests to inspect entries(). FakeAuditWriter
        // doesn't implement Clone (it has Mutex<Vec<_>>), so we go via Arc
        // and a thin newtype that defers to it.
        struct ArcWriter(std::sync::Arc<FakeAuditWriter>);
        impl AuditWriter for ArcWriter {
            fn append(&self, entry: &crate::audit_log::AuditEntry) {
                self.0.append(entry);
            }
        }
        let svc =
            GatepathHelperService::new(ops, auth, nm, throttle, Box::new(ArcWriter(audit_ref)));
        (svc, audit)
    }

    #[test]
    fn setup_with_valid_input_succeeds_and_marks_active() {
        let (svc, audit) = svc_with_audit(
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
        let (svc, audit) = svc_with_audit(
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
        // Audit still records refusal.
        assert_eq!(audit.entries().len(), 1);
        assert!(matches!(
            audit.entries()[0].decision,
            AuditDecision::Refused { .. }
        ));
    }

    #[test]
    fn setup_with_auth_denied_does_not_touch_kernel() {
        let (svc, _audit) = svc_with_audit(
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
        let (svc, _audit) = svc_with_audit(
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
        let (svc, _audit) = svc_with_audit(
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
        let (svc, _audit) = svc_with_audit(
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
        let (svc, _audit) = svc_with_audit(
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
        let (svc, _audit) = svc_with_audit(
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
        let (svc, _audit) = svc_with_audit(
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
        let (svc, audit) = svc_with_audit(
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
        // Two audit entries: setup success + teardown success.
        assert_eq!(audit.entries().len(), 2);
        assert_eq!(audit.entries()[1].action, AuditAction::TeardownCaptive);
        assert_eq!(audit.entries()[1].decision, AuditDecision::Success);
    }

    #[test]
    fn teardown_when_idle_returns_not_active() {
        let (svc, _audit) = svc_with_audit(
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
        let (svc, _audit) = svc_with_audit(
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
        // Use the regular constructor directly since svc_with_audit pins the
        // ops type to FakeNetnsOps.
        let svc = GatepathHelperService::new(
            exploding,
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
            Box::new(FakeAuditWriter::new()),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
        assert!(!svc.is_active());
    }

    // ── Phase 5b.5 NEW tests: throttle + audit ────────────────────────────

    #[test]
    fn setup_throttled_after_burst_returns_throttled() {
        // Limit 2; first 2 should pass auth/lock, 3rd should be throttled.
        let throttle = Throttle::new(2, Duration::from_secs(60));
        let (svc, _audit) = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            throttle,
        );
        let _first = svc.setup_captive(&req("wlan0"), ":1.42");
        // Second call hits AlreadyActive; throttle still recorded the call.
        let _second = svc.setup_captive(&req("wlan0"), ":1.42");
        // Third call from same sender: throttled.
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
        let (svc, _audit) = svc_with_audit(
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
        // Throttle blocks BEFORE auth — no additional auth check.
        assert_eq!(svc.auth.checks().len(), auth_count_after_first);
    }

    #[test]
    fn audit_records_refusal_with_reason_string() {
        let nm = FakeCaptiveCheck::new();
        nm.say_not_captive("wlan0");
        let (svc, audit) = svc_with_audit(
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
        let (svc, audit) = svc_with_audit(
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
}
