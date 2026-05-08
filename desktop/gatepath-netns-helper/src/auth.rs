//! Authorisation surface for the helper.
//!
//! Real builds run a PolicyKit check on every D-Bus method call (5b.3 ships
//! the `pkcheck` shell-out impl). For the service-layer logic in 5b.2, the
//! [`Authorizer`] trait is what the orchestrator calls; tests substitute
//! [`FakeAuthorizer`] to drive the allow/deny paths without a session bus.
//!
//! The shape is deliberately thin:
//!   - `check(action, sender)` returns `Ok(())` on success, an [`AuthError`]
//!     on failure.
//!   - The orchestrator translates `AuthError::Denied` into
//!     [`crate::RefusalReason::Unauthorised`] and `AuthError::Error` into
//!     [`crate::RefusalReason::KernelError`] (since auth backend failure is
//!     observably the same as a sub-system error from the user's POV).

use thiserror::Error;

/// PolicyKit action IDs the helper checks. Stable strings — referenced in
/// the .policy file shipped in 5b.3.
pub const ACTION_SETUP_CAPTIVE: &str = "cc.grepon.Gatepath.NetNsHelper.SetupCaptive";
pub const ACTION_TEARDOWN_CAPTIVE: &str = "cc.grepon.Gatepath.NetNsHelper.TeardownCaptive";

#[derive(Debug, Error, PartialEq, Eq)]
pub enum AuthError {
    #[error("authorisation denied for action '{action}' (sender={sender})")]
    Denied { action: String, sender: String },
    #[error("authorisation backend error: {0}")]
    Error(String),
}

/// Authorisation gate for privileged operations.
pub trait Authorizer {
    /// Returns `Ok(())` if `sender` is authorised to perform `action`.
    ///
    /// # Errors
    ///
    /// - [`AuthError::Denied`] if PolicyKit refused the request (most common:
    ///   user clicked Cancel on the auth prompt, or the policy doesn't
    ///   allow this user/action combination).
    /// - [`AuthError::Error`] if the auth backend itself failed (e.g.
    ///   `pkcheck` not on PATH, polkit daemon not running). Treat as a
    ///   refusal at the orchestration layer — never auto-grant.
    fn check(&self, action: &str, sender: &str) -> Result<(), AuthError>;
}

// ── Fake impl ────────────────────────────────────────────────────────────

/// In-memory [`Authorizer`] for tests. Configure mode at construction:
/// `AllowAll` always returns Ok; `DenyAll` always returns Denied;
/// `Specific { allowed }` allows only listed (action, sender) pairs.
#[cfg(test)]
pub struct FakeAuthorizer {
    mode: FakeAuthMode,
    /// Records every check the orchestrator ran. Tests assert on this to
    /// verify that auth runs *before* any privileged work.
    pub log: std::sync::Mutex<Vec<(String, String)>>,
}

#[cfg(test)]
pub enum FakeAuthMode {
    AllowAll,
    DenyAll,
    Errored(String),
}

#[cfg(test)]
impl FakeAuthorizer {
    pub fn allow_all() -> Self {
        Self {
            mode: FakeAuthMode::AllowAll,
            log: std::sync::Mutex::new(Vec::new()),
        }
    }

    pub fn deny_all() -> Self {
        Self {
            mode: FakeAuthMode::DenyAll,
            log: std::sync::Mutex::new(Vec::new()),
        }
    }

    pub fn errored(reason: impl Into<String>) -> Self {
        Self {
            mode: FakeAuthMode::Errored(reason.into()),
            log: std::sync::Mutex::new(Vec::new()),
        }
    }

    pub fn checks(&self) -> Vec<(String, String)> {
        self.log.lock().unwrap().clone()
    }
}

#[cfg(test)]
impl Authorizer for FakeAuthorizer {
    fn check(&self, action: &str, sender: &str) -> Result<(), AuthError> {
        self.log
            .lock()
            .unwrap()
            .push((action.to_string(), sender.to_string()));
        match &self.mode {
            FakeAuthMode::AllowAll => Ok(()),
            FakeAuthMode::DenyAll => Err(AuthError::Denied {
                action: action.into(),
                sender: sender.into(),
            }),
            FakeAuthMode::Errored(reason) => Err(AuthError::Error(reason.clone())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allow_all_records_call_and_succeeds() {
        let auth = FakeAuthorizer::allow_all();
        assert!(auth.check(ACTION_SETUP_CAPTIVE, ":1.42").is_ok());
        assert_eq!(
            auth.checks(),
            vec![(ACTION_SETUP_CAPTIVE.to_string(), ":1.42".to_string())],
        );
    }

    #[test]
    fn deny_all_returns_denied_with_context() {
        let auth = FakeAuthorizer::deny_all();
        let err = auth
            .check(ACTION_TEARDOWN_CAPTIVE, ":1.99")
            .expect_err("expected denied");
        assert_eq!(
            err,
            AuthError::Denied {
                action: ACTION_TEARDOWN_CAPTIVE.into(),
                sender: ":1.99".into(),
            },
        );
    }

    #[test]
    fn errored_returns_error_variant() {
        let auth = FakeAuthorizer::errored("polkit not running");
        let err = auth
            .check(ACTION_SETUP_CAPTIVE, ":1.0")
            .expect_err("expected error");
        assert_eq!(err, AuthError::Error("polkit not running".into()));
    }
}
