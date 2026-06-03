//! Resolve a D-Bus sender bus name to a Unix UID.
//!
//! Phase 5b.7's spawn path needs the caller's UID so the WebView's transient
//! `systemd-run` unit can drop to the calling user (`--uid`/`--gid`, DESK-003
//! C4) before exec'ing the runner. dbus-daemon already knows
//! the UID — every connection authenticates with `EXTERNAL` (SASL on
//! `SO_PEERCRED`) so the daemon learned the UID at connect time. We just
//! ask it via `org.freedesktop.DBus.GetConnectionUnixUser`.
//!
//! This is preferable to resolving sender → PID via D-Bus and PID → UID
//! via `/proc/<pid>/status`: that has a TOCTOU window where the original
//! process could exit and a new one with a different UID could reuse the
//! PID before we read /proc. dbus-daemon's UID record is stable for the
//! life of the connection.
//!
//! Trait abstraction so service tests don't need a real bus.

use thiserror::Error;
use zbus::blocking::Connection;
use zbus::blocking::fdo::DBusProxy;
use zbus::names::BusName;

#[derive(Debug, Error)]
pub enum CallerUidError {
    #[error("D-Bus call failed: {0}")]
    DbusFailed(String),
    #[error("invalid sender bus name '{0}'")]
    InvalidName(String),
}

/// Resolve a D-Bus unique name (`:1.42` etc.) to the connection's owner UID.
///
/// Real impl talks to the system bus; tests use [`FakeCallerUidLookup`].
pub trait CallerUidLookup: Send + Sync + 'static {
    /// # Errors
    ///
    /// - [`CallerUidError::DbusFailed`] for any zbus error during the lookup.
    /// - [`CallerUidError::InvalidName`] if `sender` isn't a valid bus name.
    fn uid_of(&self, sender: &str) -> Result<u32, CallerUidError>;
}

// ── Production impl ────────────────────────────────────────────────────

pub struct DbusCallerUidLookup {
    conn: Connection,
}

impl DbusCallerUidLookup {
    pub fn new(conn: Connection) -> Self {
        Self { conn }
    }

    /// Connect to the system bus.
    ///
    /// # Errors
    ///
    /// - System bus unreachable.
    pub fn connect() -> Result<Self, zbus::Error> {
        Ok(Self {
            conn: Connection::system()?,
        })
    }
}

impl CallerUidLookup for DbusCallerUidLookup {
    fn uid_of(&self, sender: &str) -> Result<u32, CallerUidError> {
        let bus_name = BusName::try_from(sender)
            .map_err(|e| CallerUidError::InvalidName(format!("{sender}: {e}")))?;
        let dbus =
            DBusProxy::new(&self.conn).map_err(|e| CallerUidError::DbusFailed(e.to_string()))?;
        dbus.get_connection_unix_user(bus_name)
            .map_err(|e| CallerUidError::DbusFailed(e.to_string()))
    }
}

// ── Fake impl for tests ────────────────────────────────────────────────

#[cfg(test)]
pub struct FakeCallerUidLookup {
    /// sender → UID mapping. Senders not present yield `InvalidName`.
    answers: std::sync::Mutex<std::collections::HashMap<String, u32>>,
    force_dbus_failure: std::sync::Mutex<bool>,
}

#[cfg(test)]
impl FakeCallerUidLookup {
    pub fn new() -> Self {
        Self {
            answers: std::sync::Mutex::new(std::collections::HashMap::new()),
            force_dbus_failure: std::sync::Mutex::new(false),
        }
    }

    pub fn set_uid(&self, sender: &str, uid: u32) {
        self.answers.lock().unwrap().insert(sender.into(), uid);
    }

    pub fn fail_dbus(&self) {
        *self.force_dbus_failure.lock().unwrap() = true;
    }
}

#[cfg(test)]
impl Default for FakeCallerUidLookup {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl CallerUidLookup for FakeCallerUidLookup {
    fn uid_of(&self, sender: &str) -> Result<u32, CallerUidError> {
        if *self.force_dbus_failure.lock().unwrap() {
            return Err(CallerUidError::DbusFailed("fake forced".into()));
        }
        match self.answers.lock().unwrap().get(sender) {
            Some(&uid) => Ok(uid),
            None => Err(CallerUidError::InvalidName(format!(
                "no fake mapping for {sender}"
            ))),
        }
    }
}

/// Lets tests pass an `Arc<FakeCallerUidLookup>` while keeping a separate
/// handle for assertions. Service stores `Box<dyn CallerUidLookup>`.
impl<T: CallerUidLookup> CallerUidLookup for std::sync::Arc<T> {
    fn uid_of(&self, sender: &str) -> Result<u32, CallerUidError> {
        T::uid_of(self, sender)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fake_returns_canned_uid() {
        let f = FakeCallerUidLookup::new();
        f.set_uid(":1.42", 1000);
        assert_eq!(f.uid_of(":1.42").unwrap(), 1000);
    }

    #[test]
    fn fake_returns_invalid_for_unknown_sender() {
        let f = FakeCallerUidLookup::new();
        let err = f.uid_of(":1.99").unwrap_err();
        assert!(matches!(err, CallerUidError::InvalidName(_)));
    }

    #[test]
    fn fake_propagates_forced_dbus_error() {
        let f = FakeCallerUidLookup::new();
        f.fail_dbus();
        f.set_uid(":1.42", 1000);
        let err = f.uid_of(":1.42").unwrap_err();
        assert!(matches!(err, CallerUidError::DbusFailed(_)));
    }
}
