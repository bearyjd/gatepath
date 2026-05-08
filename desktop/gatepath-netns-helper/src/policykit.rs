//! Production [`Authorizer`](crate::auth::Authorizer) backed by PolicyKit.
//!
//! Calls `org.freedesktop.PolicyKit1.Authority.CheckAuthorization` directly
//! over the system bus via zbus — no shell-out to `pkcheck`, no PID parsing.
//! The PolicyKit daemon handles user prompting (via the active session's
//! polkit agent), policy lookup against `/usr/share/polkit-1/actions/`, and
//! the AllowActiveUser/Inactive/etc. rule evaluation.
//!
//! # Subject identification
//!
//! PolicyKit needs to know *who* is asking. We use the
//! `system-bus-name` subject kind — passing the sender's D-Bus name (e.g.
//! `:1.42`) to the authority. PolicyKit resolves the sender to a PID + UID
//! by calling back to dbus-daemon. This is the canonical way to authorise a
//! D-Bus method call and avoids TOCTOU windows present in PID-based
//! identification.

use std::collections::HashMap;

use crate::auth::{AuthError, Authorizer};
use zbus::blocking::Connection;
use zbus::proxy;
use zbus::zvariant::{OwnedValue, Value};

/// PolicyKit's `CheckAuthorization` flag values. We default to
/// `AllowUserInteraction` (1) so the PolicyKit agent can prompt; tests that
/// don't want prompting can pass `None`.
const ALLOW_USER_INTERACTION: u32 = 1;

#[proxy(
    interface = "org.freedesktop.PolicyKit1.Authority",
    default_service = "org.freedesktop.PolicyKit1",
    default_path = "/org/freedesktop/PolicyKit1/Authority",
    gen_async = false
)]
trait PolicyKitAuthority {
    /// PolicyKit's authorization API. Returns `(is_authorized, is_challenge,
    /// details)`. We treat `(true, _, _)` as authorised; anything else is
    /// denied. `is_challenge=true` would indicate "needs auth dialog" — but
    /// since we passed `AllowUserInteraction`, polkitd handles the dialog
    /// internally and only returns after the user has answered.
    #[allow(clippy::too_many_arguments)]
    fn check_authorization(
        &self,
        subject: &(String, HashMap<String, OwnedValue>),
        action_id: &str,
        details: &HashMap<String, String>,
        flags: u32,
        cancellation_id: &str,
    ) -> zbus::Result<(bool, bool, HashMap<String, String>)>;
}

/// Real [`Authorizer`] that goes through `polkitd`. Constructed once at
/// helper startup and shared across all D-Bus method handlers.
pub struct PolicyKitAuthorizer {
    authority: PolicyKitAuthorityProxy<'static>,
}

impl PolicyKitAuthorizer {
    /// Connect to the system bus and resolve the PolicyKit authority proxy.
    /// Failure here is fatal — the helper should refuse to start without
    /// auth.
    ///
    /// # Errors
    ///
    /// - System bus unavailable (rare; would mean dbus-daemon isn't running)
    /// - PolicyKit service not registered (polkit not installed/running)
    pub fn connect() -> Result<Self, zbus::Error> {
        let conn = Connection::system()?;
        let authority = PolicyKitAuthorityProxy::new(&conn)?;
        Ok(Self { authority })
    }
}

impl Authorizer for PolicyKitAuthorizer {
    fn check(&self, action: &str, sender: &str) -> Result<(), AuthError> {
        // Build a "system-bus-name" subject — the canonical way to identify
        // a D-Bus caller. polkitd resolves sender→PID→UID via dbus-daemon,
        // closing the TOCTOU window vs. doing PID lookup ourselves.
        let mut subject_props = HashMap::<String, OwnedValue>::new();
        subject_props.insert(
            "name".to_string(),
            Value::from(sender)
                .try_into()
                .map_err(|e: zbus::zvariant::Error| AuthError::Error(e.to_string()))?,
        );
        let subject = ("system-bus-name".to_string(), subject_props);

        let result = self
            .authority
            .check_authorization(
                &subject,
                action,
                &HashMap::new(),
                ALLOW_USER_INTERACTION,
                "",
            )
            .map_err(|e| AuthError::Error(format!("polkit CheckAuthorization failed: {e}")))?;

        let (is_authorized, _is_challenge, _details) = result;
        if is_authorized {
            Ok(())
        } else {
            Err(AuthError::Denied {
                action: action.to_string(),
                sender: sender.to_string(),
            })
        }
    }
}
