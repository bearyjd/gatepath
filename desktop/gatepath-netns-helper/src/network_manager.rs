//! NetworkManager defence-in-depth check.
//!
//! Phase 5b.2's [`crate::service::GatepathHelperService`] validates the
//! interface name and runs PolicyKit, but neither check confirms the
//! interface is *currently captive*. Without that confirmation, a
//! compromised helper or malicious caller with valid PolicyKit creds could
//! hand any WiFi interface name (including ones the user is NOT actively
//! trying to sign into) and migrate it into the gatepath netns — disrupting
//! the user's actual home WiFi.
//!
//! This module asks NetworkManager for the device's `Connectivity`
//! property. We accept only `NM_CONNECTIVITY_PORTAL = 2` as captive. The
//! check fires between the auth pass and the kernel ops; failure short-
//! circuits with [`crate::RefusalReason::NotCaptive`].
//!
//! Trait abstraction so service tests can substitute a fake.

use thiserror::Error;
use zbus::blocking::Connection;
use zbus::names::InterfaceName;
use zbus::proxy;
use zbus::zvariant::OwnedObjectPath;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum NMError {
    #[error("interface '{0}' not found among NetworkManager devices")]
    InterfaceNotFound(String),
    #[error("NetworkManager D-Bus call failed: {0}")]
    DbusFailed(String),
}

/// `NM_CONNECTIVITY_PORTAL` per `nm-dbus-types.h`. Strict — we don't accept
/// `LIMITED` (3) because that fires for any network NM couldn't fully
/// validate, opening the helper to misuse for non-captive disruption.
const NM_CONNECTIVITY_PORTAL: u32 = 2;

#[proxy(
    interface = "org.freedesktop.NetworkManager",
    default_service = "org.freedesktop.NetworkManager",
    default_path = "/org/freedesktop/NetworkManager",
    gen_async = false
)]
trait NetworkManager {
    fn get_devices(&self) -> zbus::Result<Vec<OwnedObjectPath>>;
}

#[proxy(
    interface = "org.freedesktop.NetworkManager.Device",
    default_service = "org.freedesktop.NetworkManager",
    gen_async = false
)]
trait NMDevice {
    #[zbus(property)]
    fn interface(&self) -> zbus::Result<String>;

    #[zbus(property)]
    fn connectivity(&self) -> zbus::Result<u32>;
}

/// Trait for "is this interface currently flagged captive by NM?". Real
/// impl talks to NetworkManager over D-Bus; tests use [`FakeCaptiveCheck`].
pub trait CaptiveStateChecker {
    /// Returns `Ok(true)` if NM reports the named interface as captive,
    /// `Ok(false)` if not, and `Err(NMError)` on backend failure.
    ///
    /// # Errors
    ///
    /// - [`NMError::InterfaceNotFound`] if no NM device matches `interface`.
    /// - [`NMError::DbusFailed`] for any zbus error during the lookup.
    fn is_captive(&self, interface: &str) -> Result<bool, NMError>;
}

// ── Production impl ─────────────────────────────────────────────────────

pub struct NMCaptiveCheck {
    conn: Connection,
}

impl NMCaptiveCheck {
    /// Connect to the system bus. Failure here is fatal at helper startup
    /// (the helper requires NM to be running).
    ///
    /// # Errors
    ///
    /// - System bus unreachable
    pub fn connect() -> Result<Self, zbus::Error> {
        Ok(Self {
            conn: Connection::system()?,
        })
    }
}

impl CaptiveStateChecker for NMCaptiveCheck {
    fn is_captive(&self, interface: &str) -> Result<bool, NMError> {
        let nm =
            NetworkManagerProxy::new(&self.conn).map_err(|e| NMError::DbusFailed(e.to_string()))?;
        let device_paths = nm
            .get_devices()
            .map_err(|e| NMError::DbusFailed(e.to_string()))?;

        for path in device_paths {
            let device = NMDeviceProxy::builder(&self.conn)
                .interface(
                    InterfaceName::try_from("org.freedesktop.NetworkManager.Device")
                        .map_err(|e| NMError::DbusFailed(e.to_string()))?,
                )
                .map_err(|e| NMError::DbusFailed(e.to_string()))?
                .path(path.clone())
                .map_err(|e| NMError::DbusFailed(e.to_string()))?
                .build()
                .map_err(|e| NMError::DbusFailed(e.to_string()))?;

            let iface = device
                .interface()
                .map_err(|e| NMError::DbusFailed(e.to_string()))?;
            if iface != interface {
                continue;
            }
            let conn_state = device
                .connectivity()
                .map_err(|e| NMError::DbusFailed(e.to_string()))?;
            return Ok(conn_state == NM_CONNECTIVITY_PORTAL);
        }
        Err(NMError::InterfaceNotFound(interface.to_string()))
    }
}

// ── Fake impl for tests ──────────────────────────────────────────────────

#[cfg(test)]
pub struct FakeCaptiveCheck {
    /// Interface → captive boolean. Missing key → InterfaceNotFound.
    pub answers: std::sync::Mutex<std::collections::HashMap<String, bool>>,
    /// Override that returns NMError::DbusFailed for the test "backend down" path.
    pub force_dbus_error: std::sync::Mutex<bool>,
}

#[cfg(test)]
impl FakeCaptiveCheck {
    pub fn new() -> Self {
        Self {
            answers: std::sync::Mutex::new(std::collections::HashMap::new()),
            force_dbus_error: std::sync::Mutex::new(false),
        }
    }

    pub fn say_captive(&self, interface: &str) {
        self.answers
            .lock()
            .unwrap()
            .insert(interface.to_string(), true);
    }

    pub fn say_not_captive(&self, interface: &str) {
        self.answers
            .lock()
            .unwrap()
            .insert(interface.to_string(), false);
    }

    pub fn fail_dbus(&self) {
        *self.force_dbus_error.lock().unwrap() = true;
    }
}

#[cfg(test)]
impl Default for FakeCaptiveCheck {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl CaptiveStateChecker for FakeCaptiveCheck {
    fn is_captive(&self, interface: &str) -> Result<bool, NMError> {
        if *self.force_dbus_error.lock().unwrap() {
            return Err(NMError::DbusFailed("fake forced".into()));
        }
        match self.answers.lock().unwrap().get(interface) {
            Some(captive) => Ok(*captive),
            None => Err(NMError::InterfaceNotFound(interface.to_string())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fake_returns_canned_captive_answer() {
        let nm = FakeCaptiveCheck::new();
        nm.say_captive("wlan0");
        assert_eq!(nm.is_captive("wlan0"), Ok(true));
    }

    #[test]
    fn fake_returns_not_captive_for_validated_network() {
        let nm = FakeCaptiveCheck::new();
        nm.say_not_captive("wlan0");
        assert_eq!(nm.is_captive("wlan0"), Ok(false));
    }

    #[test]
    fn fake_returns_not_found_for_unknown_interface() {
        let nm = FakeCaptiveCheck::new();
        let err = nm.is_captive("wlan99").unwrap_err();
        assert_eq!(err, NMError::InterfaceNotFound("wlan99".into()));
    }

    #[test]
    fn fake_propagates_forced_dbus_error() {
        let nm = FakeCaptiveCheck::new();
        nm.fail_dbus();
        nm.say_captive("wlan0");
        let err = nm.is_captive("wlan0").unwrap_err();
        assert!(matches!(err, NMError::DbusFailed(_)));
    }
}
