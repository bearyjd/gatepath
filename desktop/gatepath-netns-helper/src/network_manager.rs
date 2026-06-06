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
//! This module asks NetworkManager for the device's IPv4 connectivity
//! (`Ip4Connectivity`). We accept only `NM_CONNECTIVITY_PORTAL = 2` as captive.
//! The check fires between the auth pass and the kernel ops; failure short-
//! circuits with [`crate::RefusalReason::NotCaptive`].
//!
//! NOTE: the Device interface has **no** bare `Connectivity` property — it was
//! split into `Ip4Connectivity` / `Ip6Connectivity` in NetworkManager 1.16.
//! Reading `Connectivity` raises `org.freedesktop.DBus.Error.InvalidArgs: No
//! such property`. Gatepath targets open IPv4 captive networks, so we read
//! `Ip4Connectivity`. (Caught by the mac80211_hwsim integration harness; the
//! unit tests fake `CaptiveStateChecker` and so never exercised the real
//! property name.)
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
    #[error("interface '{0}' is not associated to any access point")]
    NotAssociated(String),
    #[error("NetworkManager D-Bus call failed: {0}")]
    DbusFailed(String),
}

/// The two facts the orchestrator needs from the interface's active access
/// point, read in a single NM round-trip (one device-list walk + one AP read)
/// rather than a separate call per fact.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApState {
    /// SSID (lossy UTF-8) to re-associate to inside the netns.
    pub ssid: String,
    /// Whether the network is open (no encryption advertised).
    pub is_open: bool,
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

    /// Per-device IPv4 connectivity (`NMConnectivityState`). The Device
    /// interface has no bare `Connectivity` property (see module docs); this
    /// maps to `Ip4Connectivity`, present since NetworkManager 1.16.
    #[zbus(property, name = "Ip4Connectivity")]
    fn ip4_connectivity(&self) -> zbus::Result<u32>;

    /// Per-device IPv6 connectivity (`NMConnectivityState`). Read only for
    /// diagnostics: an IPv6-only captive portal is logged but still refused,
    /// because the in-netns re-connect path is IPv4-only. Present since
    /// NetworkManager 1.16.
    #[zbus(property, name = "Ip6Connectivity")]
    fn ip6_connectivity(&self) -> zbus::Result<u32>;
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

    /// `NM_802_11_AP_FLAGS` — bit 0 (`PRIVACY`) is set for WEP/WPA networks.
    #[zbus(property)]
    fn flags(&self) -> zbus::Result<u32>;

    /// `NM_802_11_AP_SEC` WPA flags — non-zero on a WPA-protected AP.
    #[zbus(property)]
    fn wpa_flags(&self) -> zbus::Result<u32>;

    /// `NM_802_11_AP_SEC` RSN/WPA2 flags — non-zero on a WPA2/WPA3 AP.
    #[zbus(property)]
    fn rsn_flags(&self) -> zbus::Result<u32>;
}

/// `NM_802_11_AP_FLAGS_PRIVACY` — the AP requires encryption (WEP or WPA).
const NM_AP_FLAG_PRIVACY: u32 = 0x1;

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

    /// The SSID + open/secured state of the interface's active access point,
    /// captured **before** the PHY is moved into the gatepath netns (after the
    /// move, NM can no longer see the device). Both facts come from a single NM
    /// round-trip. The helper uses `is_open` to refuse secured networks up
    /// front (only open captive networks can be re-associated inside the netns
    /// today) and hands `ssid` to [`crate::connectivity`] for wpa_supplicant.
    /// The SSID is lossy UTF-8; non-UTF-8 SSIDs (rare) are best-effort.
    ///
    /// # Errors
    ///
    /// - [`NMError::InterfaceNotFound`] if no NM device matches `interface`.
    /// - [`NMError::NotAssociated`] if the device has no active access point.
    /// - [`NMError::DbusFailed`] for any zbus error during the lookup.
    fn active_ap_state(&self, interface: &str) -> Result<ApState, NMError>;
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

    /// Proxy for the interface's currently-associated access point. Shared by
    /// `active_ssid` and `active_network_is_open`.
    fn active_ap(&self, interface: &str) -> Result<NMAccessPointProxy<'_>, NMError> {
        let dev_path = self.find_device(interface)?;
        let wireless = NMDeviceWirelessProxy::builder(&self.conn)
            .path(dev_path)
            .map_err(|e| NMError::DbusFailed(e.to_string()))?
            .build()
            .map_err(|e| NMError::DbusFailed(e.to_string()))?;
        let ap_path = wireless
            .active_access_point()
            .map_err(|e| NMError::DbusFailed(e.to_string()))?;
        // NM uses "/" for "no active access point". Distinct from DbusFailed
        // so the orchestrator can tell "device dropped its association" (a
        // transient, retryable state) from "NetworkManager is unreachable".
        if ap_path.as_str() == "/" {
            return Err(NMError::NotAssociated(interface.to_string()));
        }
        NMAccessPointProxy::builder(&self.conn)
            .path(ap_path)
            .map_err(|e| NMError::DbusFailed(e.to_string()))?
            .build()
            .map_err(|e| NMError::DbusFailed(e.to_string()))
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
                .ip4_connectivity()
                .map_err(|e| NMError::DbusFailed(e.to_string()))?;
            return match conn_state {
                NM_CONNECTIVITY_PORTAL => Ok(true),
                NM_CONNECTIVITY_UNKNOWN => Err(NMError::Pending(interface.to_string())),
                _ => {
                    // IPv4 isn't captive. Peek IPv6 purely to disambiguate
                    // "genuinely not a portal" from "captive on IPv6 only",
                    // which gatepath does not service yet (the netns re-connect
                    // path is IPv4). Log it so a field report reads as an
                    // unsupported v6 portal rather than a bare NotCaptive.
                    if let Ok(NM_CONNECTIVITY_PORTAL) = device.ip6_connectivity() {
                        tracing::warn!(
                            interface = %interface,
                            "NM reports an IPv6-only captive portal; gatepath \
                             handles IPv4 captive networks only — refusing as NotCaptive"
                        );
                    }
                    Ok(false)
                }
            };
        }
        Err(NMError::InterfaceNotFound(interface.to_string()))
    }

    fn active_ap_state(&self, interface: &str) -> Result<ApState, NMError> {
        // One device-list walk + one AP proxy → both SSID and security, so
        // setup doesn't round-trip NM twice for the same access point.
        let ap = self.active_ap(interface)?;
        let ssid_bytes = ap.ssid().map_err(|e| NMError::DbusFailed(e.to_string()))?;
        let flags = ap.flags().map_err(|e| NMError::DbusFailed(e.to_string()))?;
        let wpa = ap
            .wpa_flags()
            .map_err(|e| NMError::DbusFailed(e.to_string()))?;
        let rsn = ap
            .rsn_flags()
            .map_err(|e| NMError::DbusFailed(e.to_string()))?;
        Ok(ApState {
            ssid: String::from_utf8_lossy(&ssid_bytes).into_owned(),
            // Open == no encryption advertised at all.
            is_open: (flags & NM_AP_FLAG_PRIVACY) == 0 && wpa == 0 && rsn == 0,
        })
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
    /// Interfaces whose active network is secured. Default (absent) = open.
    pub secured: std::sync::Mutex<std::collections::HashSet<String>>,
    /// Interfaces reporting no active access point (drives `NotAssociated`).
    pub unassociated: std::sync::Mutex<std::collections::HashSet<String>>,
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
            secured: std::sync::Mutex::new(std::collections::HashSet::new()),
            unassociated: std::sync::Mutex::new(std::collections::HashSet::new()),
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

    /// Mark an interface's active network as secured (so `active_ap_state`
    /// reports `is_open == false`).
    pub fn set_secured(&self, interface: &str) {
        self.secured.lock().unwrap().insert(interface.to_string());
    }

    /// Mark an interface as having no active AP (so `active_ap_state` returns
    /// `NMError::NotAssociated`).
    pub fn set_unassociated(&self, interface: &str) {
        self.unassociated
            .lock()
            .unwrap()
            .insert(interface.to_string());
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

    fn active_ap_state(&self, interface: &str) -> Result<ApState, NMError> {
        if *self.force_dbus_error.lock().unwrap() || *self.force_ssid_error.lock().unwrap() {
            return Err(NMError::DbusFailed("fake forced".into()));
        }
        if self.unassociated.lock().unwrap().contains(interface) {
            return Err(NMError::NotAssociated(interface.to_string()));
        }
        Ok(ApState {
            ssid: self
                .ssids
                .lock()
                .unwrap()
                .get(interface)
                .cloned()
                .unwrap_or_else(|| "gatepath-test-ssid".to_string()),
            is_open: !self.secured.lock().unwrap().contains(interface),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fake_ap_state_reports_open_by_default_secured_when_set() {
        let nm = FakeCaptiveCheck::new();
        assert!(nm.active_ap_state("wlan0").unwrap().is_open);
        nm.set_secured("wlan0");
        assert!(!nm.active_ap_state("wlan0").unwrap().is_open);
        // Other interfaces stay open.
        assert!(nm.active_ap_state("wlp3s0").unwrap().is_open);
    }

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
    fn fake_ap_state_returns_set_ssid_then_default() {
        let nm = FakeCaptiveCheck::new();
        nm.set_ssid("wlan0", "CoffeeWiFi");
        assert_eq!(nm.active_ap_state("wlan0").unwrap().ssid, "CoffeeWiFi");
        // Unset interface falls back to the canned default.
        assert_eq!(
            nm.active_ap_state("wlp3s0").unwrap().ssid,
            "gatepath-test-ssid"
        );
    }

    #[test]
    fn fake_ap_state_propagates_forced_dbus_error() {
        let nm = FakeCaptiveCheck::new();
        nm.fail_dbus();
        let err = nm.active_ap_state("wlan0").unwrap_err();
        assert!(matches!(err, NMError::DbusFailed(_)));
    }
}
