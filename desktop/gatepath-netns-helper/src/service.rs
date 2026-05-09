//! Orchestration service for the helper.
//!
//! Pure-logic layer that the future zbus binary in 5b.3 will instantiate. It
//! glues five concerns together:
//!
//!   1. Validate the interface name against [`crate::validation`] (refuses
//!      VPN/tunnel/loopback/ethernet interfaces — the security spec from PR #14).
//!   2. Run the PolicyKit auth check via [`crate::auth::Authorizer`].
//!   3. Confirm with NetworkManager that the interface IS currently captive
//!      via [`crate::network_manager::CaptiveStateChecker`] (defence-in-depth
//!      added in 5b.4 — argument validation alone is not enough; an attacker
//!      with valid PolicyKit creds could otherwise target any WiFi interface).
//!   4. Drive the kernel ops via [`crate::netns::NetnsOps`].
//!   5. Track a single active session — a second concurrent setup is refused
//!      with [`crate::RefusalReason::AlreadyActive`].
//!
//! The order is deliberate. Validation runs FIRST so that even if the
//! Authorizer were to crash or the kernel ops were buggy, an attacker
//! cannot reach the privileged code paths with a malicious interface name.
//! That makes the security boundary one tested function rather than a
//! property of multiple subsystems agreeing.

use std::sync::Mutex;

use crate::{
    RefusalReason, SetupCaptiveRequest, SetupCaptiveResponse, TeardownCaptiveResponse,
    auth::{ACTION_SETUP_CAPTIVE, ACTION_TEARDOWN_CAPTIVE, AuthError, Authorizer},
    netns::NetnsOps,
    network_manager::CaptiveStateChecker,
    validation::validate_interface_name,
};

/// Fixed netns name. Helper only ever manages one captive session at a time
/// (Gatepath only has one in flight), so a constant is sufficient.
pub const NETNS_NAME: &str = "gatepath";

pub struct GatepathHelperService<N: NetnsOps, A: Authorizer, C: CaptiveStateChecker> {
    ops: N,
    auth: A,
    captive_check: C,
    /// `Some(interface_name)` while a session is active, `None` otherwise.
    /// Mutex because the D-Bus service handles concurrent calls; this field
    /// is the lock that prevents two concurrent setups racing.
    active: Mutex<Option<String>>,
}

impl<N: NetnsOps, A: Authorizer, C: CaptiveStateChecker> GatepathHelperService<N, A, C> {
    pub fn new(ops: N, auth: A, captive_check: C) -> Self {
        Self {
            ops,
            auth,
            captive_check,
            active: Mutex::new(None),
        }
    }

    /// Handle a `SetupCaptiveNetns` D-Bus call.
    pub fn setup_captive(
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

        // 2. PolicyKit.
        if let Err(err) = self.auth.check(ACTION_SETUP_CAPTIVE, sender) {
            return SetupCaptiveResponse::Refused {
                reason: refusal_for_auth_error(&err),
            };
        }

        // 3. NetworkManager defence-in-depth: confirm the requested interface
        //    is currently flagged captive. Argument validation says "this is
        //    a sane WiFi name"; this says "...and it's actually captive right
        //    now." Without this, an attacker with valid PolicyKit creds could
        //    target any WiFi interface and disrupt the user's normal network.
        match self.captive_check.is_captive(&request.interface_name) {
            Ok(true) => {}
            Ok(false) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::NotCaptive,
                };
            }
            Err(_) => {
                // NM unreachable / interface missing — treat as KernelError;
                // never auto-grant when the defence-in-depth backend is down.
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::KernelError,
                };
            }
        }

        // 4. Single-session lock — refuse a concurrent setup before touching
        //    the kernel.
        let mut active = self.active.lock().expect("active mutex poisoned");
        if active.is_some() {
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::AlreadyActive,
            };
        }

        // 5. Kernel ops. On failure, the session does NOT become active —
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
        if let Err(err) = self.auth.check(ACTION_TEARDOWN_CAPTIVE, sender) {
            tracing_error_msg(&err);
            // Auth failure on teardown is unusual — but if PolicyKit denies,
            // we must not pretend we tore something down.
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

/// Stub for future tracing wiring. Intentionally a no-op so we don't pull
/// in the `tracing` crate during 5b.2 — 5b.3 wires it up alongside the
/// audit log writer.
fn tracing_error_msg<T: std::fmt::Display>(_err: &T) {}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::FakeAuthorizer;
    use crate::netns::{FakeNetnsOps, NetnsError};
    use crate::network_manager::FakeCaptiveCheck;

    fn req(iface: &str) -> SetupCaptiveRequest {
        SetupCaptiveRequest {
            interface_name: iface.into(),
        }
    }

    /// Default-allow captive checker — says any interface passed is captive.
    /// Used by tests that aren't specifically testing the captive gate.
    fn allow_captive() -> FakeCaptiveCheck {
        let nm = FakeCaptiveCheck::new();
        nm.say_captive("wlan0");
        nm.say_captive("wlp3s0");
        nm
    }

    #[test]
    fn setup_with_valid_input_succeeds_and_marks_active() {
        let svc = GatepathHelperService::new(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        match resp {
            SetupCaptiveResponse::Success { netns_path } => {
                assert_eq!(netns_path, "/var/run/netns/gatepath");
            }
            other => panic!("expected Success, got {other:?}"),
        }
        assert!(svc.is_active());
    }

    #[test]
    fn setup_with_invalid_interface_skips_auth_and_kernel() {
        let svc = GatepathHelperService::new(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
        );
        let resp = svc.setup_captive(&req("tun0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::InvalidInterface,
            },
        );
        // Validation must fire BEFORE auth — record-only check should be empty.
        assert_eq!(
            svc.auth.checks().len(),
            0,
            "auth ran on rejected interface — validation must short-circuit",
        );
        assert!(!svc.is_active());
    }

    #[test]
    fn setup_with_auth_denied_does_not_touch_kernel() {
        let svc = GatepathHelperService::new(
            FakeNetnsOps::new(),
            FakeAuthorizer::deny_all(),
            allow_captive(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Unauthorised,
            },
        );
        assert!(
            svc.ops.netns().is_empty(),
            "netns created despite auth deny"
        );
        assert!(!svc.is_active());
    }

    #[test]
    fn setup_with_auth_backend_error_returns_kernel_error() {
        let svc = GatepathHelperService::new(
            FakeNetnsOps::new(),
            FakeAuthorizer::errored("polkit unreachable"),
            allow_captive(),
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
        let svc = GatepathHelperService::new(FakeNetnsOps::new(), FakeAuthorizer::allow_all(), nm);
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::NotCaptive,
            },
        );
        assert!(!svc.is_active());
        assert!(
            svc.ops.netns().is_empty(),
            "netns created despite NotCaptive refusal",
        );
    }

    #[test]
    fn setup_with_nm_unreachable_returns_kernel_error() {
        let nm = FakeCaptiveCheck::new();
        nm.fail_dbus();
        let svc = GatepathHelperService::new(FakeNetnsOps::new(), FakeAuthorizer::allow_all(), nm);
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
        assert!(!svc.is_active());
    }

    #[test]
    fn setup_with_unknown_interface_returns_kernel_error() {
        // FakeCaptiveCheck returns InterfaceNotFound for unseen interfaces;
        // we map that to KernelError (never auto-grant on backend errors).
        let nm = FakeCaptiveCheck::new();
        // Don't say anything about wlan0 → InterfaceNotFound.
        let svc = GatepathHelperService::new(FakeNetnsOps::new(), FakeAuthorizer::allow_all(), nm);
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
    }

    #[test]
    fn second_setup_returns_already_active() {
        let svc = GatepathHelperService::new(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
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
        let svc = GatepathHelperService::new(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(svc.is_active());
        let resp = svc.teardown_captive(":1.42");
        assert_eq!(resp, TeardownCaptiveResponse::Success);
        assert!(!svc.is_active());
    }

    #[test]
    fn teardown_when_idle_returns_not_active() {
        let svc = GatepathHelperService::new(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
        );
        let resp = svc.teardown_captive(":1.42");
        assert_eq!(resp, TeardownCaptiveResponse::NotActive);
    }

    #[test]
    fn teardown_with_auth_denied_does_not_clear_state() {
        let svc = GatepathHelperService::new(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(svc.is_active());
        let before = svc.auth.checks().len();
        let _ = svc.teardown_captive(":1.42");
        assert_eq!(svc.auth.checks().len(), before + 1);
    }

    #[test]
    fn kernel_error_during_move_rolls_back_netns() {
        // Trigger this by validating an interface the FAKE will accept
        // (passes validation and captive check) but that the fake's
        // move_interface will reject. Build a custom NetnsOps that fails on move.
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
        let inner = FakeNetnsOps::new();
        let exploding = ExplodingMoveFake { inner };
        let svc =
            GatepathHelperService::new(exploding, FakeAuthorizer::allow_all(), allow_captive());
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
        // Critical: failed move must NOT leave the session marked active —
        // otherwise the user is stuck with no path to retry.
        assert!(
            !svc.is_active(),
            "failed setup must not mark session active"
        );
    }
}
