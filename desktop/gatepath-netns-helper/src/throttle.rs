//! Per-sender rate limiter for `SetupCaptive` calls.
//!
//! Closes the prompt-fatigue DoS that survived PR #20's reorder: a malicious
//! local process can call `SetupCaptive("wlan0")` (a real captive) repeatedly
//! and force the user to see a polkit auth prompt each time. Even with
//! captive-check-before-auth (PR #20), the prompt fires whenever the call
//! gets past the captive check.
//!
//! With this throttle in place, the orchestrator refuses with
//! [`crate::RefusalReason::Throttled`] after `limit` calls in `window`
//! seconds from the same sender. The first call still hits PolicyKit (so
//! the legitimate Gatepath UI works on first try); spam attempts hit the
//! cap and stop reaching the auth subsystem.
//!
//! State is in-memory only — restarting the helper resets the counters.
//! That's acceptable because (a) the helper is socket-activated and lives
//! only as long as Gatepath UI is talking to it, and (b) a sender losing
//! its bus name (process exit) means a "new" sender on reconnect anyway.

use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Rate limiter keyed on D-Bus sender name. Sliding-window count of recent
/// `allow()` calls per sender.
pub struct Throttle {
    by_sender: Mutex<HashMap<String, VecDeque<Instant>>>,
    limit: usize,
    window: Duration,
}

impl Throttle {
    pub fn new(limit: usize, window: Duration) -> Self {
        Self {
            by_sender: Mutex::new(HashMap::new()),
            limit,
            window,
        }
    }

    /// Returns `true` if the call is under the rate limit (and records it),
    /// `false` if the cap is hit.
    ///
    /// Sliding-window: any call older than `window` is evicted before the
    /// limit check.
    pub fn allow(&self, sender: &str) -> bool {
        self.allow_at(sender, Instant::now())
    }

    /// Test-friendly: takes an injected `now` so unit tests don't have to
    /// sleep. Production calls go through [`allow`].
    fn allow_at(&self, sender: &str, now: Instant) -> bool {
        let mut by_sender = self.by_sender.lock().expect("throttle mutex poisoned");
        let entries = by_sender.entry(sender.to_string()).or_default();

        // Evict entries outside the window. They're append-ordered, so we
        // can pop from the front until the oldest is fresh enough.
        while let Some(&oldest) = entries.front() {
            if now.saturating_duration_since(oldest) >= self.window {
                entries.pop_front();
            } else {
                break;
            }
        }

        if entries.len() >= self.limit {
            return false;
        }
        entries.push_back(now);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fresh_sender_within_limit_passes() {
        let t = Throttle::new(5, Duration::from_secs(60));
        for _ in 0..5 {
            assert!(t.allow(":1.42"));
        }
    }

    #[test]
    fn sender_over_limit_is_blocked() {
        let t = Throttle::new(3, Duration::from_secs(60));
        assert!(t.allow(":1.42"));
        assert!(t.allow(":1.42"));
        assert!(t.allow(":1.42"));
        assert!(!t.allow(":1.42"), "4th call should be blocked");
        assert!(!t.allow(":1.42"), "5th call should still be blocked");
    }

    #[test]
    fn senders_are_independent() {
        let t = Throttle::new(2, Duration::from_secs(60));
        assert!(t.allow(":1.42"));
        assert!(t.allow(":1.42"));
        assert!(!t.allow(":1.42"));
        // Different sender, fresh budget.
        assert!(t.allow(":1.99"));
        assert!(t.allow(":1.99"));
        assert!(!t.allow(":1.99"));
    }

    #[test]
    fn old_entries_are_evicted_after_window() {
        let t = Throttle::new(3, Duration::from_millis(100));
        let start = Instant::now();
        // Burn the budget at t0.
        assert!(t.allow_at(":1.42", start));
        assert!(t.allow_at(":1.42", start));
        assert!(t.allow_at(":1.42", start));
        assert!(!t.allow_at(":1.42", start));
        // Move time forward past the window.
        let later = start + Duration::from_millis(150);
        assert!(
            t.allow_at(":1.42", later),
            "stale entries should be evicted"
        );
    }

    #[test]
    fn partial_eviction_keeps_recent_entries() {
        let t = Throttle::new(3, Duration::from_millis(100));
        let start = Instant::now();
        assert!(t.allow_at(":1.42", start));
        // 50ms later, two more calls.
        let mid = start + Duration::from_millis(50);
        assert!(t.allow_at(":1.42", mid));
        assert!(t.allow_at(":1.42", mid));
        // 4th would be blocked.
        assert!(!t.allow_at(":1.42", mid));
        // 120ms after start: first entry is stale, but mid entries are fresh.
        // Window evicts only the start entry, leaving 2 + new = 3. OK.
        let after_start_window = start + Duration::from_millis(120);
        assert!(t.allow_at(":1.42", after_start_window));
        // 5th call is now blocked (3 fresh entries: 2 mid + 1 new).
        assert!(!t.allow_at(":1.42", after_start_window));
    }

    #[test]
    fn empty_sender_string_is_treated_as_distinct_key() {
        // Belt + suspenders: dbus_service.rs already refuses empty sender
        // up-front with Unauthorised, but if a test bypasses that, the
        // throttle should still bucket consistently rather than panicking.
        let t = Throttle::new(1, Duration::from_secs(60));
        assert!(t.allow(""));
        assert!(!t.allow(""));
        // Different sender should still work.
        assert!(t.allow(":1.42"));
    }
}
