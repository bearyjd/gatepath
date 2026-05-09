//! Auto-teardown when the requesting D-Bus client disconnects.
//!
//! Phase 5b.6 closes the leak window where Gatepath UI crashes after
//! `SetupCaptive` succeeded but before `TeardownCaptive`. Without this
//! watch, the netns lives until SIGTERM (helper process exit). With it,
//! the helper subscribes to `org.freedesktop.DBus.NameOwnerChanged` for
//! the requesting sender; if the sender's connection drops, the helper
//! auto-tears-down the active session.
//!
//! Trait abstraction so service tests can drive disconnects without a
//! running dbus-daemon.
//!
//! ## Why no auth on the auto-teardown
//!
//! The caller of the auto-teardown path is the helper itself, in response
//! to a verified D-Bus disconnect of an already-authorised sender. Asking
//! `PolicyKit` to authorise that flow doesn't help: there's no interactive
//! session, the sender is gone, and the operation is the *recovery* path
//! after an unsupervised disconnect. Auditing it is enough.

use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use thiserror::Error;

/// Trait-object alias for the disconnect callback. Boxed so it can be
/// shipped across thread boundaries; `FnOnce` because each watch fires at
/// most once.
pub type DisconnectCallback = Box<dyn FnOnce() + Send + 'static>;

/// Internal: shared mutex around the disconnect callback. Lets the watch
/// thread and the cancel closure race on `take()` without double-firing.
type CallbackHolder = Arc<Mutex<Option<DisconnectCallback>>>;

#[derive(Debug, Error)]
pub enum WatchError {
    #[error("D-Bus signal subscription failed: {0}")]
    DbusFailed(String),
}

/// Cancellation handle for an installed name watch. Dropping the guard
/// cancels the watch — the callback will not fire afterwards. Any signal
/// observation that races with cancellation finds the callback already
/// taken and exits cleanly.
pub struct WatchGuard {
    cancel: Option<Box<dyn FnOnce() + Send>>,
}

impl WatchGuard {
    pub fn new(cancel: Box<dyn FnOnce() + Send>) -> Self {
        Self {
            cancel: Some(cancel),
        }
    }

    /// Sentinel guard used when the watch wasn't actually installed (e.g.
    /// the watched name had already disconnected and the callback fired
    /// synchronously). Drop is a no-op.
    pub fn noop() -> Self {
        Self { cancel: None }
    }
}

impl Drop for WatchGuard {
    fn drop(&mut self) {
        if let Some(cancel) = self.cancel.take() {
            cancel();
        }
    }
}

/// Installs a watch on a D-Bus unique name. Real impl talks to the
/// `org.freedesktop.DBus` interface; tests use [`FakeNameWatcher`].
pub trait NameWatcher: Send + Sync + 'static {
    /// Begin watching `name`. The callback runs at most once: when the bus
    /// reports `name` has lost its owner. Returns a guard whose Drop
    /// cancels the watch.
    ///
    /// # Errors
    ///
    /// - [`WatchError::DbusFailed`] if the underlying signal-match
    ///   subscription couldn't be installed.
    fn watch(
        &self,
        name: &str,
        on_disconnect: DisconnectCallback,
    ) -> Result<WatchGuard, WatchError>;
}

/// Lets tests pass an `Arc<FakeNameWatcher>` as the service's `W` while
/// still holding a separate handle for assertions. Production code uses
/// [`LinuxNameWatcher`] directly.
impl<T: NameWatcher> NameWatcher for Arc<T> {
    fn watch(
        &self,
        name: &str,
        on_disconnect: DisconnectCallback,
    ) -> Result<WatchGuard, WatchError> {
        T::watch(self, name, on_disconnect)
    }
}

// ── Production impl ─────────────────────────────────────────────────────

use std::thread;

use tracing::{debug, warn};
use zbus::blocking::Connection;
use zbus::blocking::fdo::DBusProxy;

pub struct LinuxNameWatcher {
    conn: Connection,
}

impl LinuxNameWatcher {
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

impl NameWatcher for LinuxNameWatcher {
    fn watch(
        &self,
        name: &str,
        on_disconnect: DisconnectCallback,
    ) -> Result<WatchGuard, WatchError> {
        let dbus = DBusProxy::new(&self.conn).map_err(|e| WatchError::DbusFailed(e.to_string()))?;

        // Subscribe BEFORE the owner-existence check below: if we checked
        // first and a disconnect happened in the gap, we'd miss it.
        let signals = dbus
            .receive_name_owner_changed()
            .map_err(|e| WatchError::DbusFailed(e.to_string()))?;

        let cb_holder: CallbackHolder = Arc::new(Mutex::new(Some(on_disconnect)));
        let cancel_flag = Arc::new(AtomicBool::new(false));
        let target = name.to_string();

        let cb_for_thread = Arc::clone(&cb_holder);
        let flag_for_thread = Arc::clone(&cancel_flag);
        let target_for_thread = target.clone();

        thread::spawn(move || {
            for signal in signals {
                if flag_for_thread.load(Ordering::SeqCst) {
                    return;
                }
                let args = match signal.args() {
                    Ok(args) => args,
                    Err(e) => {
                        warn!(error = %e, "skipping malformed NameOwnerChanged");
                        continue;
                    }
                };
                if args.name.as_str() != target_for_thread {
                    continue;
                }
                // `new_owner` is `Optional<UniqueName>`. `None` means the
                // name has lost its owner — the disconnect we care about.
                // `Some(...)` means re-acquired; not our signal.
                if args.new_owner.is_some() {
                    continue;
                }
                if let Some(cb) = cb_for_thread.lock().expect("cb mutex").take() {
                    debug!(name = %target_for_thread, "watched name disconnected");
                    cb();
                }
                return;
            }
        });

        // Catch the case where the name had already disconnected before we
        // subscribed: NameOwnerChanged won't replay, so check explicitly.
        let bus_name = name
            .try_into()
            .map_err(|e: zbus::names::Error| WatchError::DbusFailed(e.to_string()))?;
        match dbus.name_has_owner(bus_name) {
            Ok(true) => {}
            Ok(false) => {
                if let Some(cb) = cb_holder.lock().expect("cb mutex").take() {
                    cancel_flag.store(true, Ordering::SeqCst);
                    cb();
                    return Ok(WatchGuard::noop());
                }
            }
            Err(e) => {
                // Couldn't check — trust the signal stream. Log and continue.
                warn!(error = %e, "name_has_owner check failed; relying on signal stream");
            }
        }

        let cancel: Box<dyn FnOnce() + Send> = Box::new(move || {
            cancel_flag.store(true, Ordering::SeqCst);
            // Take the callback so even an in-flight signal observation
            // can't fire it after Drop returned.
            let _ = cb_holder.lock().unwrap().take();
        });
        Ok(WatchGuard::new(cancel))
    }
}

// ── Fake impl for tests ─────────────────────────────────────────────────

#[cfg(test)]
pub struct FakeNameWatcher {
    inner: Arc<Mutex<FakeInner>>,
}

#[cfg(test)]
struct FakeInner {
    /// Currently-installed callbacks per name. Removed on either explicit
    /// drop (via WatchGuard) or fire_disconnect.
    callbacks: std::collections::HashMap<String, DisconnectCallback>,
    /// Names that should fail at watch-time. Used to drive the
    /// `WatchError::DbusFailed` path.
    fail_names: std::collections::HashSet<String>,
}

#[cfg(test)]
impl FakeNameWatcher {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(FakeInner {
                callbacks: std::collections::HashMap::new(),
                fail_names: std::collections::HashSet::new(),
            })),
        }
    }

    /// Pretend the named bus connection has dropped. Fires the registered
    /// callback (if any) and removes the watch.
    pub fn fire_disconnect(&self, name: &str) {
        let cb = self.inner.lock().unwrap().callbacks.remove(name);
        if let Some(cb) = cb {
            cb();
        }
    }

    pub fn is_watching(&self, name: &str) -> bool {
        self.inner.lock().unwrap().callbacks.contains_key(name)
    }

    pub fn watched_count(&self) -> usize {
        self.inner.lock().unwrap().callbacks.len()
    }

    pub fn fail_for(&self, name: &str) {
        self.inner.lock().unwrap().fail_names.insert(name.into());
    }
}

#[cfg(test)]
impl Default for FakeNameWatcher {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl NameWatcher for FakeNameWatcher {
    fn watch(
        &self,
        name: &str,
        on_disconnect: DisconnectCallback,
    ) -> Result<WatchGuard, WatchError> {
        let mut inner = self.inner.lock().unwrap();
        if inner.fail_names.contains(name) {
            return Err(WatchError::DbusFailed("fake forced".into()));
        }
        inner.callbacks.insert(name.to_string(), on_disconnect);
        let inner_for_drop = Arc::clone(&self.inner);
        let name_for_drop = name.to_string();
        let cancel: Box<dyn FnOnce() + Send> = Box::new(move || {
            inner_for_drop
                .lock()
                .unwrap()
                .callbacks
                .remove(&name_for_drop);
        });
        Ok(WatchGuard::new(cancel))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fake_records_watch() {
        let w = FakeNameWatcher::new();
        let _g = w.watch(":1.42", Box::new(|| {})).unwrap();
        assert!(w.is_watching(":1.42"));
    }

    #[test]
    fn fake_fire_invokes_callback_and_removes_watch() {
        let w = FakeNameWatcher::new();
        let fired = Arc::new(AtomicBool::new(false));
        let fired_clone = Arc::clone(&fired);
        let _g = w
            .watch(
                ":1.42",
                Box::new(move || {
                    fired_clone.store(true, Ordering::SeqCst);
                }),
            )
            .unwrap();
        w.fire_disconnect(":1.42");
        assert!(fired.load(Ordering::SeqCst));
        assert!(!w.is_watching(":1.42"));
    }

    #[test]
    fn drop_guard_cancels_watch() {
        let w = FakeNameWatcher::new();
        let fired = Arc::new(AtomicBool::new(false));
        let fired_clone = Arc::clone(&fired);
        {
            let _g = w
                .watch(
                    ":1.42",
                    Box::new(move || {
                        fired_clone.store(true, Ordering::SeqCst);
                    }),
                )
                .unwrap();
            // guard goes out of scope here
        }
        // After drop, watch is gone. Firing has no effect.
        w.fire_disconnect(":1.42");
        assert!(!fired.load(Ordering::SeqCst));
        assert!(!w.is_watching(":1.42"));
    }

    #[test]
    fn fake_can_simulate_dbus_failure() {
        let w = FakeNameWatcher::new();
        w.fail_for(":1.99");
        let result = w.watch(":1.99", Box::new(|| {}));
        assert!(matches!(result, Err(WatchError::DbusFailed(_))));
    }

    #[test]
    fn fire_unwatched_name_is_noop() {
        let w = FakeNameWatcher::new();
        // Should not panic.
        w.fire_disconnect(":1.99");
        assert_eq!(w.watched_count(), 0);
    }

    #[test]
    fn cancel_after_fire_is_idempotent() {
        let w = FakeNameWatcher::new();
        let g = w.watch(":1.42", Box::new(|| {})).unwrap();
        w.fire_disconnect(":1.42");
        // Drop after the disconnect — must not panic, must not double-remove.
        drop(g);
        assert_eq!(w.watched_count(), 0);
    }
}
