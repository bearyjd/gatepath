//! Backstop auto-teardown if no `TeardownCaptive` arrives within 30s of
//! a subprocess exit (Phase 5b.8).
//!
//! Closes the residual leak window that 5b.6's name-watch doesn't cover:
//! the orchestrator process is still ALIVE (so name-watch doesn't fire)
//! but stuck in a long callback, deadlocked, or otherwise unable to call
//! `TeardownCaptive` after observing `PortalSubprocessExited`. Without
//! this backstop the netns + interface stay captured until SIGTERM.
//!
//! Plan locked-in duration: 30 seconds. Long enough that a healthy
//! orchestrator will always beat it (the teardown path is ~milliseconds);
//! short enough that a stuck orchestrator doesn't strand the user's WiFi
//! for minutes.
//!
//! Trait abstraction so service tests can fire the backstop synchronously
//! without a real 30-second sleep.

use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Default backstop window. Production wiring uses this; tests inject
/// shorter values via [`BackstopTimer::schedule`]'s injected duration.
pub const DEFAULT_BACKSTOP_DURATION: Duration = Duration::from_secs(30);

/// Cancellation handle for an installed backstop. Dropping the guard
/// cancels the timer — the callback will not fire afterwards. Mirrors
/// [`crate::name_watch::WatchGuard`]'s shape so the two cancellation
/// surfaces stay consistent.
pub struct BackstopGuard {
    cancel: Option<Box<dyn FnOnce() + Send>>,
}

impl BackstopGuard {
    pub fn new(cancel: Box<dyn FnOnce() + Send>) -> Self {
        Self {
            cancel: Some(cancel),
        }
    }

    /// Sentinel guard that does nothing on drop. Used when no backstop
    /// was installed (eg. exit fired but service was being torn down).
    pub fn noop() -> Self {
        Self { cancel: None }
    }
}

impl Drop for BackstopGuard {
    fn drop(&mut self) {
        if let Some(cancel) = self.cancel.take() {
            cancel();
        }
    }
}

/// Trait-object alias for the backstop fire callback. Boxed so it can
/// be shipped across thread boundaries; `FnOnce` because each backstop
/// fires at most once.
pub type BackstopCallback = Box<dyn FnOnce() + Send + 'static>;

/// Schedules a callback to run after a duration unless cancelled.
/// Real impl ([`StdThreadBackstop`]) uses `std::thread::sleep`; tests
/// use [`FakeBackstop`] to fire synchronously.
pub trait BackstopTimer: Send + Sync + 'static {
    /// Schedule `callback` to run after `duration`. Returns a guard that
    /// cancels the timer when dropped.
    fn schedule(&self, duration: Duration, callback: BackstopCallback) -> BackstopGuard;
}

// ── Production impl ────────────────────────────────────────────────────

use std::sync::mpsc;
use std::thread;

/// Production backstop: spawns a `std::thread` per call that sleeps with
/// `recv_timeout` on a cancel channel. Cancelling sends on the channel
/// (waking the thread before the sleep elapses); the thread returns
/// without firing. Otherwise the timeout elapses and the callback runs.
pub struct StdThreadBackstop;

impl StdThreadBackstop {
    pub fn new() -> Self {
        Self
    }
}

impl Default for StdThreadBackstop {
    fn default() -> Self {
        Self::new()
    }
}

impl BackstopTimer for StdThreadBackstop {
    fn schedule(&self, duration: Duration, callback: BackstopCallback) -> BackstopGuard {
        let (tx, rx) = mpsc::channel::<()>();
        let cb_holder: Arc<Mutex<Option<BackstopCallback>>> = Arc::new(Mutex::new(Some(callback)));
        let cb_for_thread = Arc::clone(&cb_holder);

        thread::spawn(move || {
            // recv_timeout returns Ok if cancelled, Err(Timeout) if the
            // duration elapsed without a message.
            if rx.recv_timeout(duration).is_ok() {
                return;
            }
            // Took the callback under the mutex so we don't double-fire
            // if a cancel races in immediately after the timeout.
            if let Some(cb) = cb_for_thread.lock().expect("backstop cb mutex").take() {
                cb();
            }
        });

        let cancel: Box<dyn FnOnce() + Send> = Box::new(move || {
            // Send fails iff the receiver is already dropped (timeout
            // already fired and exited the loop). Either way the
            // callback is gone — take it out so a late race can't fire.
            let _ = tx.send(());
            let _ = cb_holder.lock().unwrap().take();
        });
        BackstopGuard::new(cancel)
    }
}

// ── Fake impl for tests ────────────────────────────────────────────────

#[cfg(test)]
use std::sync::atomic::{AtomicBool, Ordering};

#[cfg(test)]
pub struct FakeBackstop {
    inner: Arc<Mutex<FakeInner>>,
}

#[cfg(test)]
struct FakeInner {
    /// Currently-pending callback, removed on either explicit drop (via
    /// BackstopGuard) or `fire`.
    pending: Option<BackstopCallback>,
    /// Last duration requested. Tests assert against the configured 30s.
    last_duration: Option<Duration>,
    schedule_count: usize,
    cancelled: AtomicBool,
}

#[cfg(test)]
impl FakeBackstop {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(FakeInner {
                pending: None,
                last_duration: None,
                schedule_count: 0,
                cancelled: AtomicBool::new(false),
            })),
        }
    }

    /// Synchronously fire the pending callback. Mirrors what the real
    /// timer would do when its sleep elapses.
    pub fn fire(&self) {
        let cb = self.inner.lock().unwrap().pending.take();
        if let Some(cb) = cb {
            cb();
        }
    }

    pub fn last_duration(&self) -> Option<Duration> {
        self.inner.lock().unwrap().last_duration
    }

    pub fn schedule_count(&self) -> usize {
        self.inner.lock().unwrap().schedule_count
    }

    pub fn was_cancelled(&self) -> bool {
        self.inner.lock().unwrap().cancelled.load(Ordering::SeqCst)
    }

    pub fn has_pending(&self) -> bool {
        self.inner.lock().unwrap().pending.is_some()
    }
}

#[cfg(test)]
impl Default for FakeBackstop {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl BackstopTimer for FakeBackstop {
    fn schedule(&self, duration: Duration, callback: BackstopCallback) -> BackstopGuard {
        let mut inner = self.inner.lock().unwrap();
        inner.last_duration = Some(duration);
        inner.schedule_count += 1;
        inner.pending = Some(callback);
        let inner_for_drop = Arc::clone(&self.inner);
        let cancel: Box<dyn FnOnce() + Send> = Box::new(move || {
            let mut g = inner_for_drop.lock().unwrap();
            g.cancelled.store(true, Ordering::SeqCst);
            g.pending = None;
        });
        BackstopGuard::new(cancel)
    }
}

/// Lets tests pass an `Arc<FakeBackstop>` while keeping a separate
/// handle for assertions. Service stores `Box<dyn BackstopTimer>`.
impl<T: BackstopTimer> BackstopTimer for Arc<T> {
    fn schedule(&self, duration: Duration, callback: BackstopCallback) -> BackstopGuard {
        T::schedule(self, duration, callback)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fake_records_schedule_request() {
        let b = FakeBackstop::new();
        let _g = b.schedule(Duration::from_secs(30), Box::new(|| {}));
        assert_eq!(b.last_duration(), Some(Duration::from_secs(30)));
        assert_eq!(b.schedule_count(), 1);
        assert!(b.has_pending());
    }

    #[test]
    fn fake_fire_invokes_callback_and_clears_pending() {
        let b = FakeBackstop::new();
        let fired = Arc::new(AtomicBool::new(false));
        let fired_clone = Arc::clone(&fired);
        let _g = b.schedule(
            Duration::from_secs(30),
            Box::new(move || {
                fired_clone.store(true, Ordering::SeqCst);
            }),
        );
        b.fire();
        assert!(fired.load(Ordering::SeqCst));
        assert!(!b.has_pending());
    }

    #[test]
    fn drop_guard_cancels_backstop() {
        let b = FakeBackstop::new();
        let fired = Arc::new(AtomicBool::new(false));
        let fired_clone = Arc::clone(&fired);
        {
            let _g = b.schedule(
                Duration::from_secs(30),
                Box::new(move || {
                    fired_clone.store(true, Ordering::SeqCst);
                }),
            );
        }
        // Cancelled.
        assert!(b.was_cancelled());
        b.fire(); // no-op
        assert!(!fired.load(Ordering::SeqCst));
    }

    #[test]
    fn fire_after_cancel_is_noop() {
        let b = FakeBackstop::new();
        let g = b.schedule(Duration::from_secs(30), Box::new(|| {}));
        drop(g);
        b.fire();
        // Should not panic; pending is None.
        assert!(!b.has_pending());
    }

    #[test]
    fn std_thread_backstop_fires_after_short_duration() {
        let bs = StdThreadBackstop::new();
        let fired = Arc::new(AtomicBool::new(false));
        let fired_clone = Arc::clone(&fired);
        let _g = bs.schedule(
            Duration::from_millis(50),
            Box::new(move || {
                fired_clone.store(true, Ordering::SeqCst);
            }),
        );
        thread::sleep(Duration::from_millis(200));
        assert!(fired.load(Ordering::SeqCst));
    }

    #[test]
    fn std_thread_backstop_cancel_prevents_fire() {
        let bs = StdThreadBackstop::new();
        let fired = Arc::new(AtomicBool::new(false));
        let fired_clone = Arc::clone(&fired);
        {
            let _g = bs.schedule(
                Duration::from_millis(200),
                Box::new(move || {
                    fired_clone.store(true, Ordering::SeqCst);
                }),
            );
            // guard drops immediately, cancelling
        }
        thread::sleep(Duration::from_millis(300));
        assert!(!fired.load(Ordering::SeqCst));
    }
}
