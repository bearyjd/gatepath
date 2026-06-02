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
    #[error("NetworkManager is still evaluating connectivity for '{0}'")]
    Pending(String),
    #[error("NetworkManager D-Bus call failed: {0}")]
    DbusFailed(String),
}

/// Connectivity state values per `nm-dbus-types.h`. We accept only `PORTAL`
/// as captive; `LIMITED` is excluded because it fires for any network NM
/// couldn't fully validate, opening the helper to misuse for non-captive
/// disruption. `UNKNOWN` returns [`NMError::Pending`] so the UI can show
/// "retry" instead of "not captive" — race against NM's poll loop.
const NM_CONNECTIVITY_UNKNOWN: u32 = 0;
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

#[proxy(
    interface = "org.freedesktop.NetworkManager.Device.Wireless",
    default_service = "org.freedesktop.NetworkManager",
    gen_async = false
)]
trait NMDeviceWireless {
    /// Object path of the access point the device is currently associated
    /// with, or `/` when not associated.
    #[zbus(property)]
    fn active_access_point(&self) -> zbus::Result<OwnedObjectPath>;
}

#[proxy(
    interface = "org.freedesktop.NetworkManager.AccessPoint",
    default_service = "org.freedesktop.NetworkManager",
    gen_async = false
)]
trait NMAccessPoint {
    /// SSID as raw bytes (`ay`) — NM does not assume it is UTF-8.
    #[zbus(property)]
    fn ssid(&self) -> zbus::Result<Vec<u8>>;
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

    /// The SSID the interface is currently associated with, captured **before**
    /// the PHY is moved into the gatepath netns (after the move, NM can no
    /// longer see the device). The helper hands this to
    /// [`crate::connectivity`] so wpa_supplicant can re-associate inside the
    /// netns. Returned lossily as UTF-8; non-UTF-8 SSIDs (rare) are
    /// best-effort.
    ///
    /// # Errors
    ///
    /// - [`NMError::InterfaceNotFound`] if no NM device matches `interface`.
    /// - [`NMError::DbusFailed`] if the device isn't associated to an access
    ///   point, or for any zbus error during the lookup.
    fn active_ssid(&self, interface: &str) -> Result<String, NMError>;
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

    /// Resolve `interface` to its NetworkManager device object path.
    fn find_device(&self, interface: &str) -> Result<OwnedObjectPath, NMError> {
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
            if iface == interface {
                return Ok(path);
            }
        }
        Err(NMError::InterfaceNotFound(interface.to_string()))
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
            return match conn_state {
                NM_CONNECTIVITY_PORTAL => Ok(true),
                NM_CONNECTIVITY_UNKNOWN => Err(NMError::Pending(interface.to_string())),
                _ => Ok(false),
            };
        }
        Err(NMError::InterfaceNotFound(interface.to_string()))
    }

    fn active_ssid(&self, interface: &str) -> Result<String, NMError> {
        let dev_path = self.find_device(interface)?;
        let wireless = NMDeviceWirelessProxy::builder(&self.conn)
            .path(dev_path)
            .map_err(|e| NMError::DbusFailed(e.to_string()))?
            .build()
            .map_err(|e| NMError::DbusFailed(e.to_string()))?;
        let ap_path = wireless
            .active_access_point()
            .map_err(|e| NMError::DbusFailed(e.to_string()))?;
        // NM uses "/" for "no active access point".
        if ap_path.as_str() == "/" {
            return Err(NMError::DbusFailed(format!(
                "interface '{interface}' is not associated to an access point"
            )));
        }
        let ap = NMAccessPointProxy::builder(&self.conn)
            .path(ap_path)
            .map_err(|e| NMError::DbusFailed(e.to_string()))?
            .build()
            .map_err(|e| NMError::DbusFailed(e.to_string()))?;
        let ssid_bytes = ap.ssid().map_err(|e| NMError::DbusFailed(e.to_string()))?;
        Ok(String::from_utf8_lossy(&ssid_bytes).into_owned())
    }
}

// ── Fake impl for tests ──────────────────────────────────────────────────

#[cfg(test)]
pub struct FakeCaptiveCheck {
    /// Interface → captive boolean. Missing key → InterfaceNotFound.
    pub answers: std::sync::Mutex<std::collections::HashMap<String, bool>>,
    /// Interfaces flagged "NM still evaluating" — yields `NMError::Pending`.
    /// Checked before [`answers`]; presence here wins.
    pub pending: std::sync::Mutex<std::collections::HashSet<String>>,
    /// Override that returns `NMError::DbusFailed` for the test "backend down" path.
    pub force_dbus_error: std::sync::Mutex<bool>,
    /// Interface → active SSID. Missing key falls back to a canned default so
    /// the many setup-success tests don't each have to set one.
    pub ssids: std::sync::Mutex<std::collections::HashMap<String, String>>,
    /// Fail ONLY `active_ssid` (not `is_captive`) — lets a test drive the
    /// "captive check passes but SSID capture fails" orchestration branch.
    pub force_ssid_error: std::sync::Mutex<bool>,
}

#[cfg(test)]
impl FakeCaptiveCheck {
    pub fn new() -> Self {
        Self {
            answers: std::sync::Mutex::new(std::collections::HashMap::new()),
            pending: std::sync::Mutex::new(std::collections::HashSet::new()),
            force_dbus_error: std::sync::Mutex::new(false),
            ssids: std::sync::Mutex::new(std::collections::HashMap::new()),
            force_ssid_error: std::sync::Mutex::new(false),
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

    pub fn say_pending(&self, interface: &str) {
        self.pending.lock().unwrap().insert(interface.to_string());
    }

    pub fn set_ssid(&self, interface: &str, ssid: &str) {
        self.ssids
            .lock()
            .unwrap()
            .insert(interface.to_string(), ssid.to_string());
    }

    /// Make `active_ssid` fail while leaving `is_captive` working.
    pub fn fail_ssid(&self) {
        *self.force_ssid_error.lock().unwrap() = true;
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
        if self.pending.lock().unwrap().contains(interface) {
            return Err(NMError::Pending(interface.to_string()));
        }
        match self.answers.lock().unwrap().get(interface) {
            Some(captive) => Ok(*captive),
            None => Err(NMError::InterfaceNotFound(interface.to_string())),
        }
    }

    fn active_ssid(&self, interface: &str) -> Result<String, NMError> {
        if *self.force_dbus_error.lock().unwrap() || *self.force_ssid_error.lock().unwrap() {
            return Err(NMError::DbusFailed("fake forced".into()));
        }
        Ok(self
            .ssids
            .lock()
            .unwrap()
            .get(interface)
            .cloned()
            .unwrap_or_else(|| "gatepath-test-ssid".to_string()))
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

    #[test]
    fn fake_active_ssid_returns_set_value_then_default() {
        let nm = FakeCaptiveCheck::new();
        nm.set_ssid("wlan0", "CoffeeWiFi");
        assert_eq!(nm.active_ssid("wlan0"), Ok("CoffeeWiFi".to_string()));
        // Unset interface falls back to the canned default.
        assert_eq!(
            nm.active_ssid("wlp3s0"),
            Ok("gatepath-test-ssid".to_string())
        );
    }

    #[test]
    fn fake_active_ssid_propagates_forced_dbus_error() {
        let nm = FakeCaptiveCheck::new();
        nm.fail_dbus();
        let err = nm.active_ssid("wlan0").unwrap_err();
        assert!(matches!(err, NMError::DbusFailed(_)));
    }
}
