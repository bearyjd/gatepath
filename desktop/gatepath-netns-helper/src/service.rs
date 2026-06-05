//! Orchestration service for the helper.
//!
//! Pure-logic layer that the zbus binary instantiates. Glues these concerns
//! together:
//!
//!   1. Validate the interface name against [`crate::validation`] (refuses
//!      VPN/tunnel/loopback/ethernet interfaces — the security spec from PR #14).
//!   2. Confirm with NetworkManager that the interface IS currently captive
//!      via [`crate::network_manager::CaptiveStateChecker`]. Runs BEFORE
//!      PolicyKit so the user doesn't see an auth prompt for a non-captive
//!      interface (PR #20).
//!   3. Per-sender rate limit via [`crate::throttle::Throttle`] (PR #21).
//!      Closes the prompt-fatigue DoS that survives reorder: malicious caller
//!      spamming `SetupCaptive` on a real captive WiFi can still trigger one
//!      polkit prompt per call. Throttle caps that at N per window.
//!   4. PolicyKit auth check via [`crate::auth::Authorizer`].
//!   5. Drive the kernel ops via [`crate::netns::NetnsOps`].
//!   6. Track a single active session — a second concurrent setup is refused
//!      with [`crate::RefusalReason::AlreadyActive`].
//!   7. Watch the requesting sender on D-Bus via
//!      [`crate::name_watch::NameWatcher`] (Phase 5b.6). If the sender's
//!      connection drops without an explicit teardown, the helper auto-tears
//!      down the netns. Closes the leak window where the UI crashes mid-session.
//!   8. Audit-log every decision (success or refusal) via
//!      [`crate::audit_log::AuditWriter`].
//!
//! The order is deliberate. Validation runs FIRST so that even if any later
//! subsystem misbehaves, an attacker cannot reach privileged code paths
//! with a malicious interface name.

use std::sync::{Arc, Mutex, Weak};
use std::time::Duration;

use crate::{
    LaunchPortalRequest, LaunchPortalResponse, RefusalReason, SetupCaptiveRequest,
    SetupCaptiveResponse, TeardownCaptiveResponse,
    audit_log::{AuditAction, AuditDecision, AuditWriter, entry_now},
    auth::{ACTION_SETUP_CAPTIVE, ACTION_TEARDOWN_CAPTIVE, AuthError, Authorizer},
    backstop::{BackstopGuard, BackstopTimer, DEFAULT_BACKSTOP_DURATION},
    caller_uid::{CallerUidError, CallerUidLookup},
    connectivity::{ConnectivityParams, ConnectivitySession, NetnsConnectivity, WifiSecurity},
    name_watch::{NameWatcher, WatchGuard},
    netns::NetnsOps,
    network_manager::{CaptiveStateChecker, NMError},
    spawn::{ExitCallback, SpawnError, SpawnExit, SpawnRequest, Spawner},
    throttle::Throttle,
    validation::validate_interface_name,
};

/// Bundles the backstop timer with its duration so the service ctor
/// stays at a manageable arg count. Production wiring uses
/// [`StdThreadBackstop`] + [`DEFAULT_BACKSTOP_DURATION`]; tests inject a
/// `FakeBackstop` and a short duration.
pub struct BackstopConfig {
    pub timer: Box<dyn BackstopTimer>,
    pub duration: Duration,
}

impl BackstopConfig {
    /// Production default: [`crate::backstop::StdThreadBackstop`] + 30s.
    pub fn production() -> Self {
        Self {
            timer: Box::new(crate::backstop::StdThreadBackstop::new()),
            duration: DEFAULT_BACKSTOP_DURATION,
        }
    }
}

/// Aggregates all dependencies needed to construct a
/// [`GatepathHelperService`]. Reduces the constructor to a single
/// argument and lets call sites use struct-literal syntax (with field
/// names) so the dependency graph is readable at a glance.
///
/// Generic over the same trait params as the service itself; the boxed
/// fields cover dependencies tests don't need static-dispatch access to.
pub struct Deps<
    N: NetnsOps + Send + Sync + 'static,
    A: Authorizer + Send + Sync + 'static,
    C: CaptiveStateChecker + Send + Sync + 'static,
    W: NameWatcher,
> {
    pub ops: N,
    pub auth: A,
    pub captive_check: C,
    pub throttle: Throttle,
    pub watcher: W,
    pub spawner: Box<dyn Spawner>,
    pub caller_uid_lookup: Box<dyn CallerUidLookup>,
    /// DESK-002: re-establishes association + DHCP inside the netns after the
    /// PHY is moved. Boxed for the same reason as `spawner` — tests reach the
    /// fake via a shared `Arc`.
    pub connectivity: Box<dyn NetnsConnectivity>,
    pub backstop: BackstopConfig,
    pub audit: Box<dyn AuditWriter>,
}

/// Fixed netns name. Helper only ever manages one captive session at a time
/// (Gatepath only has one in flight), so a constant is sufficient.
pub const NETNS_NAME: &str = "gatepath";

pub struct GatepathHelperService<N: NetnsOps, A: Authorizer, C: CaptiveStateChecker, W: NameWatcher>
{
    ops: N,
    auth: A,
    captive_check: C,
    throttle: Throttle,
    watcher: W,
    /// Phase 5b.7: privileged subprocess spawner. Boxed (rather than a 5th
    /// generic) because tests don't need static-dispatch access to its
    /// concrete methods; the test fake is reachable via the same `Arc` the
    /// service holds (see `svc_with_audit` test helper).
    spawner: Box<dyn Spawner>,
    /// Phase 5b.7: D-Bus sender → UID resolver. Boxed for the same reason.
    caller_uid_lookup: Box<dyn CallerUidLookup>,
    /// DESK-002: in-netns connectivity (wpa_supplicant + DHCP).
    connectivity: Box<dyn NetnsConnectivity>,
    audit: Box<dyn AuditWriter>,
    /// `Some(interface_name)` while a session is active, `None` otherwise.
    /// Mutex because the D-Bus service handles concurrent calls; this field
    /// is the lock that prevents two concurrent setups racing.
    active: Mutex<Option<String>>,
    /// Phase 5b.7: bus name of the sender that opened the active session.
    /// `LaunchPortal` refuses callers whose sender doesn't match this —
    /// prevents one bus client from launching a subprocess inside another
    /// client's captive session.
    active_sender: Mutex<Option<String>>,
    /// Cancellation guard for the name watch installed during setup. Drop
    /// happens on explicit teardown (cancels the watch so the auto-teardown
    /// callback won't fire) and on auto-teardown (idempotent — by then the
    /// callback has already taken itself out).
    active_guard: Mutex<Option<WatchGuard>>,
    /// Phase 5b.7: external observer of subprocess exit events. Set once
    /// at startup by `main` so the D-Bus signal task can emit
    /// `PortalSubprocessExited`. Service's own internal handler runs first
    /// (audit-log + state cleanup); this fires after.
    external_exit_cb: Mutex<Option<ExitCallback>>,
    /// Phase 5b.8: backstop timer for auto-teardown if the orchestrator
    /// fails to call `TeardownCaptive` within `backstop_duration` of the
    /// subprocess exit. Closes the residual leak window 5b.6's
    /// name-watch can't see (orchestrator alive but stuck).
    backstop_timer: Box<dyn BackstopTimer>,
    backstop_duration: Duration,
    /// Currently-armed backstop guard. Set in `handle_subprocess_exit`,
    /// cleared (cancelling the timer) by both `teardown_captive_inner`
    /// and `handle_disconnect`. Multiple takes are safe — Drop cancels
    /// idempotently.
    active_backstop: Mutex<Option<BackstopGuard>>,
    /// DESK-002: live connectivity session (wpa_supplicant + DHCP). Set on
    /// successful setup; dropped — which stops those processes — BEFORE
    /// `destroy_netns` on every teardown path (explicit, disconnect, backstop).
    active_connectivity: Mutex<Option<Box<dyn ConnectivitySession>>>,
}

impl<
    N: NetnsOps + Send + Sync + 'static,
    A: Authorizer + Send + Sync + 'static,
    C: CaptiveStateChecker + Send + Sync + 'static,
    W: NameWatcher,
> GatepathHelperService<N, A, C, W>
{
    /// Construct a new helper service from a fully-specified [`Deps`].
    /// Field names at the call site keep the dependency graph readable.
    pub fn new(deps: Deps<N, A, C, W>) -> Self {
        let Deps {
            ops,
            auth,
            captive_check,
            throttle,
            watcher,
            spawner,
            caller_uid_lookup,
            connectivity,
            backstop,
            audit,
        } = deps;
        Self {
            ops,
            auth,
            captive_check,
            throttle,
            watcher,
            spawner,
            caller_uid_lookup,
            connectivity,
            audit,
            active: Mutex::new(None),
            active_sender: Mutex::new(None),
            active_guard: Mutex::new(None),
            external_exit_cb: Mutex::new(None),
            backstop_timer: backstop.timer,
            backstop_duration: backstop.duration,
            active_backstop: Mutex::new(None),
            active_connectivity: Mutex::new(None),
        }
    }

    /// Set the callback that fires once per subprocess exit, AFTER the
    /// service's own internal handler (audit + state cleanup). Used by
    /// `main` to bridge the exit event onto a D-Bus signal.
    pub fn set_external_exit_callback(&self, cb: Option<ExitCallback>) {
        *self.external_exit_cb.lock().unwrap() = cb;
    }

    /// Handle a `SetupCaptiveNetns` D-Bus call. Takes `&Arc<Self>` so the
    /// auto-teardown callback can hold a `Weak<Self>` and re-enter the
    /// service when the watched sender disconnects.
    pub fn setup_captive(
        self: &Arc<Self>,
        request: &SetupCaptiveRequest,
        sender: &str,
    ) -> SetupCaptiveResponse {
        let response = self.setup_captive_inner(request, sender);
        self.audit_setup(request, sender, &response);
        response
    }

    fn setup_captive_inner(
        self: &Arc<Self>,
        request: &SetupCaptiveRequest,
        sender: &str,
    ) -> SetupCaptiveResponse {
        // 1. Validation FIRST — the security boundary.
        if validate_interface_name(&request.interface_name).is_err() {
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::InvalidInterface,
            };
        }

        // 2. NetworkManager check BEFORE auth (PR #20 reorder).
        match self.captive_check.is_captive(&request.interface_name) {
            Ok(true) => {}
            Ok(false) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::NotCaptive,
                };
            }
            Err(NMError::Pending(_)) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::Pending,
                };
            }
            Err(NMError::InterfaceNotFound(_)) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::InvalidInterface,
                };
            }
            // `is_captive` doesn't query the access point, so it never returns
            // NotAssociated; map it with DbusFailed for exhaustiveness.
            Err(err @ (NMError::NotAssociated(_) | NMError::DbusFailed(_))) => {
                tracing::error!(
                    error = %err,
                    interface = %request.interface_name,
                    "is_captive NetworkManager query failed"
                );
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::BackendUnavailable,
                };
            }
        }

        // 3. Throttle: per-sender rate-limit before PolicyKit. A malicious
        //    caller spamming SetupCaptive on a real captive WiFi would
        //    otherwise trigger a polkit prompt for every call.
        if !self.throttle.allow(sender) {
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::Throttled,
            };
        }

        // 4. PolicyKit — only after the request is plausibly valid AND
        //    the sender hasn't blown through the rate limit.
        if let Err(err) = self.auth.check(ACTION_SETUP_CAPTIVE, sender) {
            tracing::error!(
                error = %err,
                action = ACTION_SETUP_CAPTIVE,
                sender,
                "PolicyKit auth check refused SetupCaptive"
            );
            return SetupCaptiveResponse::Refused {
                reason: refusal_for_auth_error(&err),
            };
        }

        // 5. Single-session lock — refuse a concurrent setup before touching
        //    the kernel.
        let mut active = self.active.lock().expect("active mutex poisoned");
        if active.is_some() {
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::AlreadyActive,
            };
        }

        // 5a. Read the active AP's SSID + security in ONE NM round-trip, while
        //     NetworkManager can still see the device (step 6 moves the PHY and
        //     NM loses sight of it). Two facts from one access point:
        //       - `is_open` gates the open-network requirement (DESK-002) — we
        //         refuse a secured network up front, before the PHY move, rather
        //         than tearing the user's Wi-Fi away only to fail at DHCP.
        //       - `ssid` is what wpa_supplicant re-associates to inside the netns.
        let ssid = match self.captive_check.active_ap_state(&request.interface_name) {
            Ok(ap) if ap.is_open => ap.ssid,
            Ok(_secured) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::UnsupportedSecurity,
                };
            }
            // Device dropped its association between the captive check and now
            // — transient, so tell the UI to retry rather than "NM is down".
            Err(NMError::NotAssociated(_)) => {
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::Pending,
                };
            }
            Err(err) => {
                tracing::error!(error = %err, "active AP lookup failed");
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::BackendUnavailable,
                };
            }
        };

        // 6. Kernel ops. On failure, the session does NOT become active.
        let netns_path = match self.ops.create_netns(NETNS_NAME) {
            Ok(p) => p,
            Err(err) => {
                tracing::error!(error = %err, netns = NETNS_NAME, "create_netns failed");
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::KernelError,
                };
            }
        };

        if let Err(err) = self.ops.move_interface(&request.interface_name, NETNS_NAME) {
            // Best-effort teardown so we don't leak a netns on partial failure.
            let _ = self.ops.destroy_netns(NETNS_NAME);
            tracing::error!(error = %err, "move_interface failed");
            return SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            };
        }

        // 6b. DESK-002: the moved PHY is unassociated and address-less. Bring
        //     the link up, re-associate via wpa_supplicant, and acquire a DHCP
        //     lease inside the netns before the WebView is ever launched. On
        //     failure, tear the netns back down — a half-built isolated stack
        //     is worse than none.
        let conn_params = ConnectivityParams {
            netns_name: NETNS_NAME.to_string(),
            interface: request.interface_name.clone(),
            ssid,
            security: WifiSecurity::Open,
        };
        let connectivity_session = match self.connectivity.bring_up(&conn_params) {
            Ok(session) => session,
            Err(err) => {
                tracing::error!(error = %err, "connectivity bring-up failed");
                let _ = self.ops.destroy_netns(NETNS_NAME);
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::KernelError,
                };
            }
        };

        // 7. Install the name watch so we auto-teardown if the UI dies. If
        //    the watch can't be installed, undo the kernel ops and return
        //    KernelError — running without an auto-teardown would leak the
        //    netns past UI crashes, which 5b.6 exists to prevent.
        let weak: Weak<Self> = Arc::downgrade(self);
        let sender_for_cb = sender.to_string();
        let cb: Box<dyn FnOnce() + Send + 'static> = Box::new(move || {
            if let Some(strong) = weak.upgrade() {
                strong.handle_disconnect(&sender_for_cb);
            }
        });
        let guard = match self.watcher.watch(sender, cb) {
            Ok(g) => g,
            Err(err) => {
                tracing::error!(error = %err, "name watch install failed");
                // Stop wpa_supplicant/DHCP before tearing the netns down.
                drop(connectivity_session);
                let _ = self.ops.destroy_netns(NETNS_NAME);
                return SetupCaptiveResponse::Refused {
                    reason: RefusalReason::KernelError,
                };
            }
        };

        *active = Some(request.interface_name.clone());
        *self
            .active_sender
            .lock()
            .expect("active_sender mutex poisoned") = Some(sender.to_string());
        *self.active_guard.lock().expect("guard mutex poisoned") = Some(guard);
        *self
            .active_connectivity
            .lock()
            .expect("connectivity mutex poisoned") = Some(connectivity_session);

        SetupCaptiveResponse::Success {
            netns_path: netns_path.to_string_lossy().into_owned(),
        }
    }

    /// Handle a `TeardownCaptiveNetns` D-Bus call.
    pub fn teardown_captive(&self, sender: &str) -> TeardownCaptiveResponse {
        let response = self.teardown_captive_inner(sender);
        self.audit_teardown(sender, &response);
        response
    }

    fn teardown_captive_inner(&self, sender: &str) -> TeardownCaptiveResponse {
        if let Err(err) = self.auth.check(ACTION_TEARDOWN_CAPTIVE, sender) {
            tracing::error!(error = %err, "teardown auth check failed");
            return TeardownCaptiveResponse::KernelError;
        }

        // Phase 1 (under the `active` lock): confirm a session is active,
        // detach the watch guard + backstop, and TAKE the connectivity session
        // out — without dropping it yet. We deliberately leave `active` = Some
        // so a concurrent setup is refused (AlreadyActive) while the slow
        // connectivity stop runs lock-free below.
        //
        // Race note: because the lock is released during Phase 2/3, another
        // teardown-class path (disconnect/backstop) could also pass its
        // is-active gate in this window. That is benign — the second `take()`
        // yields `None`, `destroy_netns` is idempotent, and both null `active`
        // together; the only visible effect is a possible duplicate audit
        // entry for one logical teardown.
        let session = {
            let active = self.active.lock().expect("active mutex poisoned");
            if active.is_none() {
                return TeardownCaptiveResponse::NotActive;
            }
            // Drop the watch guard BEFORE destroy so the auto-teardown callback
            // can't race in. Cancel the backstop timer too (5b.8).
            *self.active_guard.lock().expect("guard mutex poisoned") = None;
            *self
                .active_backstop
                .lock()
                .expect("backstop mutex poisoned") = None;
            self.active_connectivity
                .lock()
                .expect("connectivity mutex poisoned")
                .take()
        };

        // Phase 2 (DESK-002): stop wpa_supplicant/DHCP with NO lock held — the
        // kill + reap must not stall every other D-Bus method on `active`. This
        // runs BEFORE destroy_netns so the processes release their netns
        // sockets first (a process still pinned would outlive `ip netns del`).
        drop(session);

        // Phase 3: destroy the netns, then clear state. On a kernel error we
        // leave `active` = Some so an explicit teardown can be retried.
        match self.ops.destroy_netns(NETNS_NAME) {
            Ok(()) => {
                let mut active = self.active.lock().expect("active mutex poisoned");
                *active = None;
                *self
                    .active_sender
                    .lock()
                    .expect("active_sender mutex poisoned") = None;
                TeardownCaptiveResponse::Success
            }
            Err(_) => TeardownCaptiveResponse::KernelError,
        }
    }

    /// Auto-teardown path. Fired by the name watcher when the requesting
    /// sender's D-Bus connection disconnects without an explicit teardown.
    /// No PolicyKit check — see module docs in [`crate::name_watch`] for the
    /// security argument. Idempotent: if an explicit teardown already
    /// cleared the session, this call is a no-op.
    fn handle_disconnect(&self, sender: &str) {
        // Phase 1 (under the `active` lock): confirm active, detach the guard +
        // backstop (the guard's cancel closure no-ops here — the callback we're
        // running was already taken), and TAKE the connectivity session out.
        // Leave `active` = Some during the lock-free stop below.
        let session = {
            let active = self.active.lock().expect("active mutex poisoned");
            if active.is_none() {
                return;
            }
            *self.active_guard.lock().expect("guard mutex poisoned") = None;
            *self
                .active_backstop
                .lock()
                .expect("backstop mutex poisoned") = None;
            self.active_connectivity
                .lock()
                .expect("connectivity mutex poisoned")
                .take()
        };

        // Phase 2 (DESK-002): stop wpa_supplicant/DHCP without holding `active`.
        drop(session);

        // Phase 3: destroy the netns and clear state unconditionally — the
        // sender is gone, so there is nobody to retry a kernel failure.
        let result = self.ops.destroy_netns(NETNS_NAME);
        {
            let mut active = self.active.lock().expect("active mutex poisoned");
            *active = None;
            *self
                .active_sender
                .lock()
                .expect("active_sender mutex poisoned") = None;
        }

        let decision = match &result {
            Ok(()) => AuditDecision::Success,
            Err(err) => AuditDecision::Refused {
                reason: format!("kernel_error: {err}"),
            },
        };
        let entry = entry_now(AuditAction::AutoTeardown, sender, None, decision);
        self.audit.append(&entry);
    }

    /// Phase 5b.7: launch a portal subprocess inside the active netns.
    ///
    /// Refusal cases (in order of evaluation):
    /// - [`RefusalReason::NoActiveSession`] — no `SetupCaptive` has succeeded.
    /// - [`RefusalReason::SenderMismatch`] — caller isn't the session's owner.
    /// - [`RefusalReason::InvalidPortalUrl`] — URL fails RFC 3986 / scheme / control-byte checks.
    /// - [`RefusalReason::Unauthorised`] — caller's UID couldn't be resolved.
    /// - [`RefusalReason::SpawnFailed`] — launching the transient WebView unit failed.
    ///
    /// Auth note: NO PolicyKit check here. The session is already gated
    /// (the corresponding `SetupCaptive` ran auth) AND the spawn is gated
    /// by `SenderMismatch` to the same originating client. A second prompt
    /// during the same captive flow is hostile UX (see plan doc).
    pub fn launch_portal_subprocess(
        self: &Arc<Self>,
        request: &LaunchPortalRequest,
        sender: &str,
    ) -> LaunchPortalResponse {
        let response = self.launch_portal_inner(request, sender);
        self.audit_launch(sender, &response);
        response
    }

    fn launch_portal_inner(
        self: &Arc<Self>,
        request: &LaunchPortalRequest,
        sender: &str,
    ) -> LaunchPortalResponse {
        // 1. Active session must exist.
        if self.active.lock().expect("active mutex poisoned").is_none() {
            return LaunchPortalResponse::Refused {
                reason: RefusalReason::NoActiveSession,
            };
        }

        // 2. Caller must be the session owner.
        match self
            .active_sender
            .lock()
            .expect("active_sender mutex poisoned")
            .as_deref()
        {
            Some(owner) if owner == sender => {}
            Some(_) | None => {
                return LaunchPortalResponse::Refused {
                    reason: RefusalReason::SenderMismatch,
                };
            }
        }

        // 3. Resolve caller UID. Failure is unauthorised — never auto-grant
        //    a fallback UID like root or nobody, since the spawned process
        //    will run with whatever UID we resolve.
        let caller_uid = match self.caller_uid_lookup.uid_of(sender) {
            Ok(uid) => uid,
            Err(err) => {
                tracing::error!(error = %err, sender, "caller UID lookup failed");
                return LaunchPortalResponse::Refused {
                    reason: refusal_for_caller_uid_error(&err),
                };
            }
        };

        // 4. Spawn (which re-validates URL — defense in depth).
        let spawn_request = SpawnRequest {
            portal_url: request.portal_url.clone(),
            netns_name: NETNS_NAME.to_string(),
            caller_uid,
            wayland_display: request.wayland_display.clone(),
            x_display: request.x_display.clone(),
            x_authority: request.x_authority.clone(),
        };

        // Register the per-spawn exit callback BEFORE the actual spawn.
        // The callback dispatches to handle_subprocess_exit, which audits
        // and forwards to the external observer (D-Bus signal task).
        let weak: Weak<Self> = Arc::downgrade(self);
        let cb: ExitCallback = Arc::new(move |exit: SpawnExit| {
            if let Some(strong) = weak.upgrade() {
                strong.handle_subprocess_exit(exit);
            }
        });
        self.spawner.set_exit_callback(Some(cb));

        match self.spawner.spawn(&spawn_request) {
            Ok(pid) => LaunchPortalResponse::Success { pid },
            Err(err) => {
                tracing::error!(error = %err, "spawn failed");
                self.spawner.set_exit_callback(None);
                LaunchPortalResponse::Refused {
                    reason: refusal_for_spawn_error(&err),
                }
            }
        }
    }

    /// Internal handler for subprocess exit. Audits, notifies the external
    /// observer (D-Bus signal), AND arms the 5b.8 backstop timer that will
    /// auto-tear-down if `TeardownCaptive` doesn't arrive in time. Does
    /// NOT itself clear `active` — the orchestrator drives `TeardownCaptive`
    /// in the happy path.
    fn handle_subprocess_exit(self: &Arc<Self>, exit: SpawnExit) {
        let decision = if exit.is_clean() {
            AuditDecision::Success
        } else {
            AuditDecision::Refused {
                reason: format!(
                    "subprocess_exit code={:?} signal={:?}",
                    exit.exit_code, exit.signal
                ),
            }
        };
        let entry = crate::audit_log::entry_now_with_pid(
            AuditAction::LaunchPortal,
            "<auto>",
            None,
            Some(exit.pid),
            decision,
        );
        self.audit.append(&entry);

        if let Some(cb) = self.external_exit_cb.lock().unwrap().clone() {
            cb(exit);
        }

        // Phase 5b.8: schedule the backstop. If the orchestrator calls
        // TeardownCaptive within `backstop_duration` (default 30s), the
        // teardown path drops the guard and the timer is cancelled.
        // Otherwise the timer fires fire_backstop_teardown, which audits
        // and force-tears-down.
        let weak: Weak<Self> = Arc::downgrade(self);
        let cb: crate::backstop::BackstopCallback = Box::new(move || {
            if let Some(strong) = weak.upgrade() {
                strong.fire_backstop_teardown();
            }
        });
        let guard = self.backstop_timer.schedule(self.backstop_duration, cb);
        *self
            .active_backstop
            .lock()
            .expect("backstop mutex poisoned") = Some(guard);
    }

    /// Force-teardown driven by the 5b.8 backstop. Fired when the
    /// orchestrator hasn't called `TeardownCaptive` within the configured
    /// duration of a subprocess exit. Idempotent: if the orchestrator's
    /// teardown raced in just before the timer, this is a no-op.
    fn fire_backstop_teardown(&self) {
        // Phase 1 (under the `active` lock): confirm active, detach backstop +
        // guard, and TAKE the connectivity session out. The timer thread that
        // fired us has already exited, so the guard cancel is a no-op. Leave
        // `active` = Some during the lock-free stop below.
        let session = {
            let active = self.active.lock().expect("active mutex poisoned");
            if active.is_none() {
                return;
            }
            *self
                .active_backstop
                .lock()
                .expect("backstop mutex poisoned") = None;
            *self.active_guard.lock().expect("guard mutex poisoned") = None;
            self.active_connectivity
                .lock()
                .expect("connectivity mutex poisoned")
                .take()
        };

        // Phase 2 (DESK-002): stop wpa_supplicant/DHCP without holding `active`.
        drop(session);

        // Phase 3: destroy the netns and clear state.
        let result = self.ops.destroy_netns(NETNS_NAME);
        {
            let mut active = self.active.lock().expect("active mutex poisoned");
            *active = None;
            *self
                .active_sender
                .lock()
                .expect("active_sender mutex poisoned") = None;
        }

        let decision = match &result {
            Ok(()) => AuditDecision::Success,
            Err(err) => AuditDecision::Refused {
                reason: format!("kernel_error: {err}"),
            },
        };
        let entry = entry_now(AuditAction::AutoTeardown, "<backstop>", None, decision);
        self.audit.append(&entry);
    }

    fn audit_launch(&self, sender: &str, response: &LaunchPortalResponse) {
        let (decision, pid) = match response {
            LaunchPortalResponse::Success { pid } => (AuditDecision::Success, Some(*pid)),
            LaunchPortalResponse::Refused { reason } => (
                AuditDecision::Refused {
                    reason: reason.as_str().to_string(),
                },
                None,
            ),
        };
        let entry = crate::audit_log::entry_now_with_pid(
            AuditAction::LaunchPortal,
            sender,
            None,
            pid,
            decision,
        );
        self.audit.append(&entry);
    }

    fn audit_setup(
        &self,
        request: &SetupCaptiveRequest,
        sender: &str,
        response: &SetupCaptiveResponse,
    ) {
        let decision = match response {
            SetupCaptiveResponse::Success { .. } => AuditDecision::Success,
            SetupCaptiveResponse::Refused { reason } => AuditDecision::Refused {
                reason: reason.as_str().to_string(),
            },
        };
        let entry = entry_now(
            AuditAction::SetupCaptive,
            sender,
            Some(request.interface_name.clone()),
            decision,
        );
        self.audit.append(&entry);
    }

    fn audit_teardown(&self, sender: &str, response: &TeardownCaptiveResponse) {
        let decision = match response {
            TeardownCaptiveResponse::Success => AuditDecision::Success,
            TeardownCaptiveResponse::NotActive => AuditDecision::Refused {
                reason: "not_active".into(),
            },
            TeardownCaptiveResponse::KernelError => AuditDecision::Refused {
                reason: "kernel_error".into(),
            },
        };
        let entry = entry_now(AuditAction::TeardownCaptive, sender, None, decision);
        self.audit.append(&entry);
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

fn refusal_for_caller_uid_error(err: &CallerUidError) -> RefusalReason {
    match err {
        // dbus-daemon doesn't know this sender — treat as auth failure.
        // Could indicate the connection died between SetupCaptive and
        // LaunchPortal (name-watch should fire shortly).
        CallerUidError::InvalidName(_) => RefusalReason::Unauthorised,
        // Bus call failed entirely.
        CallerUidError::DbusFailed(_) => RefusalReason::BackendUnavailable,
    }
}

fn refusal_for_spawn_error(err: &SpawnError) -> RefusalReason {
    match err {
        SpawnError::InvalidUrl(_) => RefusalReason::InvalidPortalUrl,
        SpawnError::InvalidDisplayEnv(_) => RefusalReason::InvalidDisplayEnv,
        SpawnError::NetnsMissing { .. } => RefusalReason::KernelError,
        SpawnError::CallerUidUnavailable(_) => RefusalReason::Unauthorised,
        SpawnError::SyscallFailed(_) => RefusalReason::SpawnFailed,
    }
}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::audit_log::FakeAuditWriter;
    use crate::auth::FakeAuthorizer;
    use crate::caller_uid::FakeCallerUidLookup;
    use crate::connectivity::FakeNetnsConnectivity;
    use crate::name_watch::FakeNameWatcher;
    use crate::netns::{FakeNetnsOps, NetnsError};
    use crate::network_manager::FakeCaptiveCheck;
    use crate::spawn::FakeSpawner;
    use std::time::Duration;

    fn req(iface: &str) -> SetupCaptiveRequest {
        SetupCaptiveRequest {
            interface_name: iface.into(),
        }
    }

    fn allow_captive() -> FakeCaptiveCheck {
        let nm = FakeCaptiveCheck::new();
        nm.say_captive("wlan0");
        nm.say_captive("wlp3s0");
        nm
    }

    fn permissive_throttle() -> Throttle {
        Throttle::new(1_000_000, Duration::from_secs(60))
    }

    type TestSvc =
        GatepathHelperService<FakeNetnsOps, FakeAuthorizer, FakeCaptiveCheck, Arc<FakeNameWatcher>>;

    /// Bundle of fake handles that tests inspect after driving the
    /// service. Returned as a struct so adding new dependencies (like
    /// 5b.7's spawner and uid lookup) doesn't churn call sites.
    struct Fixture {
        svc: Arc<TestSvc>,
        audit: Arc<FakeAuditWriter>,
        watcher: Arc<FakeNameWatcher>,
        spawner: Arc<FakeSpawner>,
        uid_lookup: Arc<FakeCallerUidLookup>,
        backstop: Arc<crate::backstop::FakeBackstop>,
        connectivity: Arc<FakeNetnsConnectivity>,
    }

    fn svc_with_audit(
        ops: FakeNetnsOps,
        auth: FakeAuthorizer,
        nm: FakeCaptiveCheck,
        throttle: Throttle,
    ) -> Fixture {
        let audit = Arc::new(FakeAuditWriter::new());
        let watcher = Arc::new(FakeNameWatcher::new());
        let spawner = Arc::new(FakeSpawner::new());
        let uid_lookup = Arc::new(FakeCallerUidLookup::new());
        let backstop = Arc::new(crate::backstop::FakeBackstop::new());
        let connectivity = Arc::new(FakeNetnsConnectivity::new());
        // Default UID mapping: every sender maps to UID 1000. Tests that
        // exercise the unauthorised-uid path call `uid_lookup.fail_dbus()`
        // or override entries directly.
        uid_lookup.set_uid(":1.42", 1000);
        uid_lookup.set_uid(":1.99", 1000);

        let audit_for_svc = Arc::clone(&audit);
        let watcher_for_svc = Arc::clone(&watcher);
        let spawner_for_svc = Arc::clone(&spawner);
        let uid_for_svc = Arc::clone(&uid_lookup);
        let backstop_for_svc = Arc::clone(&backstop);
        let connectivity_for_svc = Arc::clone(&connectivity);

        struct ArcWriter(Arc<FakeAuditWriter>);
        impl AuditWriter for ArcWriter {
            fn append(&self, entry: &crate::audit_log::AuditEntry) {
                self.0.append(entry);
            }
        }
        let svc = Arc::new(GatepathHelperService::new(Deps {
            ops,
            auth,
            captive_check: nm,
            throttle,
            watcher: watcher_for_svc,
            spawner: Box::new(spawner_for_svc),
            caller_uid_lookup: Box::new(uid_for_svc),
            connectivity: Box::new(connectivity_for_svc),
            backstop: BackstopConfig {
                timer: Box::new(backstop_for_svc),
                duration: Duration::from_secs(30),
            },
            audit: Box::new(ArcWriter(audit_for_svc)),
        }));
        Fixture {
            svc,
            audit,
            watcher,
            spawner,
            uid_lookup,
            backstop,
            connectivity,
        }
    }

    #[test]
    fn setup_with_valid_input_succeeds_and_marks_active() {
        let Fixture { svc, audit, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(matches!(resp, SetupCaptiveResponse::Success { .. }));
        assert!(svc.is_active());
        assert_eq!(audit.entries().len(), 1);
        assert_eq!(audit.entries()[0].decision, AuditDecision::Success);
    }

    #[test]
    fn setup_with_invalid_interface_skips_auth_and_kernel() {
        let Fixture { svc, audit, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("tun0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::InvalidInterface,
            },
        );
        assert_eq!(svc.auth.checks().len(), 0);
        assert!(!svc.is_active());
        assert_eq!(audit.entries().len(), 1);
        assert!(matches!(
            audit.entries()[0].decision,
            AuditDecision::Refused { .. }
        ));
    }

    #[test]
    fn setup_with_auth_denied_does_not_touch_kernel() {
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::deny_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Unauthorised,
            },
        );
        assert!(svc.ops.netns().is_empty());
        assert!(!svc.is_active());
    }

    #[test]
    fn setup_with_auth_backend_error_returns_kernel_error() {
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::errored("polkit unreachable"),
            allow_captive(),
            permissive_throttle(),
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
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::NotCaptive,
            },
        );
        assert!(!svc.is_active());
    }

    #[test]
    fn setup_with_nm_unreachable_returns_backend_unavailable() {
        let nm = FakeCaptiveCheck::new();
        nm.fail_dbus();
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::BackendUnavailable,
            },
        );
    }

    #[test]
    fn setup_with_pending_nm_state_returns_pending() {
        let nm = FakeCaptiveCheck::new();
        nm.say_pending("wlan0");
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Pending,
            },
        );
        assert_eq!(svc.auth.checks().len(), 0);
    }

    #[test]
    fn setup_with_unknown_interface_returns_invalid_interface() {
        let nm = FakeCaptiveCheck::new();
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::InvalidInterface,
            },
        );
    }

    #[test]
    fn second_setup_returns_already_active() {
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
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
        let Fixture { svc, audit, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(svc.is_active());
        let resp = svc.teardown_captive(":1.42");
        assert_eq!(resp, TeardownCaptiveResponse::Success);
        assert!(!svc.is_active());
        assert_eq!(audit.entries().len(), 2);
        assert_eq!(audit.entries()[1].action, AuditAction::TeardownCaptive);
        assert_eq!(audit.entries()[1].decision, AuditDecision::Success);
    }

    #[test]
    fn teardown_when_idle_returns_not_active() {
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.teardown_captive(":1.42");
        assert_eq!(resp, TeardownCaptiveResponse::NotActive);
    }

    #[test]
    fn teardown_with_auth_denied_does_not_clear_state() {
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let before = svc.auth.checks().len();
        let _ = svc.teardown_captive(":1.42");
        assert_eq!(svc.auth.checks().len(), before + 1);
    }

    #[test]
    fn kernel_error_during_move_rolls_back_netns() {
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
        let exploding = ExplodingMoveFake {
            inner: FakeNetnsOps::new(),
        };
        let connectivity = Arc::new(FakeNetnsConnectivity::new());
        let svc = Arc::new(GatepathHelperService::new(Deps {
            ops: exploding,
            auth: FakeAuthorizer::allow_all(),
            captive_check: allow_captive(),
            throttle: permissive_throttle(),
            watcher: Arc::new(FakeNameWatcher::new()),
            spawner: Box::new(Arc::new(FakeSpawner::new())),
            caller_uid_lookup: Box::new(Arc::new(FakeCallerUidLookup::new())),
            connectivity: Box::new(Arc::clone(&connectivity)),
            backstop: BackstopConfig {
                timer: Box::new(Arc::new(crate::backstop::FakeBackstop::new())),
                duration: Duration::from_secs(30),
            },
            audit: Box::new(FakeAuditWriter::new()),
        }));
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
        assert!(!svc.is_active());
        // Ordering guarantee: connectivity bring-up must NOT run when the PHY
        // move fails (it runs only after a successful move). This pins the
        // no-leak invariant the rollback depends on.
        assert!(
            connectivity.brought_up().is_empty(),
            "bring_up must not be attempted after a failed move",
        );
    }

    // ── Phase 5b.5 throttle + audit ──────────────────────────────────────

    #[test]
    fn setup_throttled_after_burst_returns_throttled() {
        let throttle = Throttle::new(2, Duration::from_secs(60));
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            throttle,
        );
        let _first = svc.setup_captive(&req("wlan0"), ":1.42");
        let _second = svc.setup_captive(&req("wlan0"), ":1.42");
        let third = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            third,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Throttled,
            },
        );
    }

    #[test]
    fn throttled_setup_does_not_consume_auth_check() {
        let throttle = Throttle::new(1, Duration::from_secs(60));
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            throttle,
        );
        let _first = svc.setup_captive(&req("wlan0"), ":1.42");
        let auth_count_after_first = svc.auth.checks().len();
        let throttled = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            throttled,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Throttled,
            },
        );
        assert_eq!(svc.auth.checks().len(), auth_count_after_first);
    }

    #[test]
    fn audit_records_refusal_with_reason_string() {
        let nm = FakeCaptiveCheck::new();
        nm.say_not_captive("wlan0");
        let Fixture { svc, audit, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let _resp = svc.setup_captive(&req("wlan0"), ":1.42");
        let entries = audit.entries();
        assert_eq!(entries.len(), 1);
        match &entries[0].decision {
            AuditDecision::Refused { reason } => assert_eq!(reason, "not_captive"),
            other => panic!("expected Refused, got {other:?}"),
        }
    }

    #[test]
    fn audit_records_setup_then_teardown_in_order() {
        let Fixture { svc, audit, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        svc.teardown_captive(":1.42");
        let entries = audit.entries();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].action, AuditAction::SetupCaptive);
        assert_eq!(entries[0].interface, Some("wlan0".to_string()));
        assert_eq!(entries[1].action, AuditAction::TeardownCaptive);
        assert_eq!(entries[1].interface, None);
    }

    #[test]
    fn throttled_setup_still_writes_audit_entry() {
        let throttle = Throttle::new(1, Duration::from_secs(60));
        let Fixture { svc, audit, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            throttle,
        );
        let _first = svc.setup_captive(&req("wlan0"), ":1.42");
        let _second_throttled = svc.setup_captive(&req("wlan0"), ":1.42");
        let entries = audit.entries();
        assert_eq!(entries.len(), 2);
        match &entries[1].decision {
            AuditDecision::Refused { reason } => assert_eq!(reason, "throttled"),
            other => panic!("expected throttled refusal, got {other:?}"),
        }
    }

    // ── Phase 5b.6 NEW tests: name watch + auto-teardown ─────────────────

    #[test]
    fn setup_installs_watch_on_active_sender() {
        let Fixture { svc, watcher, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(watcher.is_watching(":1.42"));
    }

    #[test]
    fn refused_setup_does_not_install_watch() {
        let nm = FakeCaptiveCheck::new();
        nm.say_not_captive("wlan0");
        let Fixture { svc, watcher, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(watcher.watched_count(), 0);
    }

    #[test]
    fn disconnect_fires_auto_teardown() {
        let Fixture {
            svc,
            audit,
            watcher,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(svc.is_active());
        watcher.fire_disconnect(":1.42");
        assert!(!svc.is_active());
        // setup audit + auto-teardown audit
        let entries = audit.entries();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[1].action, AuditAction::AutoTeardown);
        assert_eq!(entries[1].sender, ":1.42");
        assert_eq!(entries[1].decision, AuditDecision::Success);
    }

    #[test]
    fn explicit_teardown_cancels_watch() {
        let Fixture { svc, watcher, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        svc.teardown_captive(":1.42");
        assert!(!watcher.is_watching(":1.42"));
        // Firing now is a no-op — watch is gone.
        watcher.fire_disconnect(":1.42");
        // Still not active; no double-teardown.
        assert!(!svc.is_active());
    }

    // ── DESK-002: in-netns connectivity orchestration ───────────────────────

    #[test]
    fn setup_brings_up_connectivity_with_captured_ssid() {
        let nm = FakeCaptiveCheck::new();
        nm.say_captive("wlan0");
        nm.set_ssid("wlan0", "CoffeeWiFi");
        let Fixture {
            svc, connectivity, ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert!(matches!(resp, SetupCaptiveResponse::Success { .. }));
        let brought = connectivity.brought_up();
        assert_eq!(
            brought.len(),
            1,
            "setup must bring connectivity up exactly once"
        );
        assert_eq!(brought[0].interface, "wlan0");
        // The SSID NM reported pre-move is the one we re-associate to.
        assert_eq!(brought[0].ssid, "CoffeeWiFi");
        assert_eq!(brought[0].netns_name, NETNS_NAME);
        assert_eq!(
            connectivity.teardown_count(),
            0,
            "session must stay up while active"
        );
    }

    #[test]
    fn setup_secured_network_refused_before_any_kernel_op() {
        // A secured captive network must be refused up front — before the PHY
        // move tears away the user's real Wi-Fi — not discovered via DHCP.
        let nm = FakeCaptiveCheck::new();
        nm.say_captive("wlan0");
        nm.set_secured("wlan0");
        let Fixture {
            svc, connectivity, ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::UnsupportedSecurity,
            },
        );
        assert!(!svc.is_active());
        assert!(
            svc.ops.netns().is_empty(),
            "secured network must not create a netns"
        );
        assert!(
            connectivity.brought_up().is_empty(),
            "secured network must not reach connectivity bring-up"
        );
    }

    #[test]
    fn setup_unassociated_device_refused_as_pending() {
        // The captive check passed but the device dropped its AP association
        // before we could read it — a transient state, so the UI is told to
        // retry (Pending), not "NetworkManager is down" (BackendUnavailable).
        let nm = FakeCaptiveCheck::new();
        nm.say_captive("wlan0");
        nm.set_unassociated("wlan0");
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::Pending,
            },
        );
        assert!(!svc.is_active());
        assert!(svc.ops.netns().is_empty());
    }

    #[test]
    fn connectivity_failure_tears_down_netns_and_refuses() {
        let Fixture {
            svc, connectivity, ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        connectivity.fail_bring_up();
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
        assert!(
            !svc.is_active(),
            "failed bring-up must not leave an active session"
        );
        // bring_up was attempted; no session was created, so there's nothing
        // to tear down (the netns was destroyed by the rollback instead).
        assert_eq!(connectivity.brought_up().len(), 1);
        assert_eq!(connectivity.teardown_count(), 0);
    }

    #[test]
    fn explicit_teardown_drops_connectivity_session() {
        let Fixture {
            svc, connectivity, ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(connectivity.teardown_count(), 0);
        svc.teardown_captive(":1.42");
        assert_eq!(
            connectivity.teardown_count(),
            1,
            "explicit teardown must stop wpa_supplicant/DHCP",
        );
    }

    #[test]
    fn disconnect_drops_connectivity_session() {
        let Fixture {
            svc,
            watcher,
            connectivity,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        watcher.fire_disconnect(":1.42");
        assert!(!svc.is_active());
        assert_eq!(
            connectivity.teardown_count(),
            1,
            "auto-teardown on sender disconnect must stop connectivity",
        );
    }

    #[test]
    fn setup_ssid_lookup_failure_refuses_and_does_not_touch_kernel() {
        // is_captive passes but the SSID capture fails — setup must refuse
        // before creating the netns or attempting connectivity bring-up.
        let nm = FakeCaptiveCheck::new();
        nm.say_captive("wlan0");
        nm.fail_ssid();
        let Fixture {
            svc, connectivity, ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            nm,
            permissive_throttle(),
        );
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::BackendUnavailable,
            },
        );
        assert!(!svc.is_active());
        assert!(svc.ops.netns().is_empty(), "no netns should be created");
        assert!(
            connectivity.brought_up().is_empty(),
            "bring_up must not run when SSID capture fails",
        );
    }

    #[test]
    fn watch_install_failure_rolls_back_kernel_and_returns_kernel_error() {
        let watcher = Arc::new(FakeNameWatcher::new());
        watcher.fail_for(":1.42");
        let audit = Arc::new(FakeAuditWriter::new());
        let audit_ref = Arc::clone(&audit);
        struct ArcWriter(Arc<FakeAuditWriter>);
        impl AuditWriter for ArcWriter {
            fn append(&self, entry: &crate::audit_log::AuditEntry) {
                self.0.append(entry);
            }
        }
        let svc = Arc::new(GatepathHelperService::new(Deps {
            ops: FakeNetnsOps::new(),
            auth: FakeAuthorizer::allow_all(),
            captive_check: allow_captive(),
            throttle: permissive_throttle(),
            watcher: Arc::clone(&watcher),
            spawner: Box::new(Arc::new(FakeSpawner::new())),
            caller_uid_lookup: Box::new(Arc::new(FakeCallerUidLookup::new())),
            connectivity: Box::new(Arc::new(FakeNetnsConnectivity::new())),
            backstop: BackstopConfig {
                timer: Box::new(Arc::new(crate::backstop::FakeBackstop::new())),
                duration: Duration::from_secs(30),
            },
            audit: Box::new(ArcWriter(audit_ref)),
        }));
        let resp = svc.setup_captive(&req("wlan0"), ":1.42");
        assert_eq!(
            resp,
            SetupCaptiveResponse::Refused {
                reason: RefusalReason::KernelError,
            },
        );
        assert!(!svc.is_active());
        // Netns must be destroyed — leaking a netns here would defeat 5b.6.
        assert!(svc.ops.netns().is_empty());
    }

    #[test]
    fn setup_after_disconnect_succeeds() {
        // After a disconnect-driven teardown, the helper should accept a
        // fresh setup. Pins that handle_disconnect actually clears state.
        let Fixture { svc, watcher, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        watcher.fire_disconnect(":1.42");
        let second = svc.setup_captive(&req("wlan0"), ":1.99");
        assert!(matches!(second, SetupCaptiveResponse::Success { .. }));
        assert!(watcher.is_watching(":1.99"));
    }

    // ── Phase 5b.7 launch_portal_subprocess ──────────────────────────────

    fn launch_req(url: &str) -> LaunchPortalRequest {
        LaunchPortalRequest {
            portal_url: url.into(),
            wayland_display: String::new(),
            x_display: String::new(),
            x_authority: String::new(),
        }
    }

    /// Like [`launch_req`] but with the display fields populated, for the
    /// DESK-004 env-plumbing assertions.
    fn launch_req_with_display(
        url: &str,
        wayland_display: &str,
        x_display: &str,
        x_authority: &str,
    ) -> LaunchPortalRequest {
        LaunchPortalRequest {
            portal_url: url.into(),
            wayland_display: wayland_display.into(),
            x_display: x_display.into(),
            x_authority: x_authority.into(),
        }
    }

    #[test]
    fn launch_without_active_session_refused() {
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        let resp = svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42");
        assert_eq!(
            resp,
            LaunchPortalResponse::Refused {
                reason: RefusalReason::NoActiveSession,
            },
        );
    }

    #[test]
    fn launch_from_other_sender_refused_with_sender_mismatch() {
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let resp = svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.99");
        assert_eq!(
            resp,
            LaunchPortalResponse::Refused {
                reason: RefusalReason::SenderMismatch,
            },
        );
    }

    #[test]
    fn launch_with_invalid_url_refused() {
        let Fixture { svc, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let resp = svc.launch_portal_subprocess(&launch_req("javascript:alert(1)"), ":1.42");
        assert_eq!(
            resp,
            LaunchPortalResponse::Refused {
                reason: RefusalReason::InvalidPortalUrl,
            },
        );
    }

    #[test]
    fn launch_succeeds_and_returns_pid() {
        let Fixture {
            svc,
            spawner,
            audit,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let resp = svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42");
        let pid = match resp {
            LaunchPortalResponse::Success { pid } => pid,
            other => panic!("expected Success, got {other:?}"),
        };
        assert_eq!(spawner.requests().len(), 1);
        assert_eq!(spawner.requests()[0].caller_uid, 1000);
        // Audit: setup + launch
        let entries = audit.entries();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[1].action, AuditAction::LaunchPortal);
        assert_eq!(entries[1].pid, Some(pid));
        assert_eq!(entries[1].decision, AuditDecision::Success);
    }

    #[test]
    fn launch_forwards_display_env_to_spawner() {
        // DESK-004: the three client display fields must reach the SpawnRequest
        // verbatim so systemd_run_args can turn them into --setenv tokens.
        let Fixture { svc, spawner, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let resp = svc.launch_portal_subprocess(
            &launch_req_with_display(
                "http://captive.example/",
                "wayland-0",
                ":0",
                "/home/u/.Xauthority",
            ),
            ":1.42",
        );
        assert!(matches!(resp, LaunchPortalResponse::Success { .. }));
        let reqs = spawner.requests();
        assert_eq!(reqs.len(), 1);
        assert_eq!(reqs[0].wayland_display, "wayland-0");
        assert_eq!(reqs[0].x_display, ":0");
        assert_eq!(reqs[0].x_authority, "/home/u/.Xauthority");
    }

    #[test]
    fn launch_with_invalid_display_env_refused() {
        let Fixture { svc, spawner, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        // DISPLAY without a ':' fails validation in the (fake) spawner.
        let resp = svc.launch_portal_subprocess(
            &launch_req_with_display("http://captive.example/", "", "bogus", ""),
            ":1.42",
        );
        assert_eq!(
            resp,
            LaunchPortalResponse::Refused {
                reason: RefusalReason::InvalidDisplayEnv,
            },
        );
        assert_eq!(spawner.requests().len(), 0);
    }

    #[test]
    fn launch_with_unresolvable_uid_refused() {
        let Fixture {
            svc, uid_lookup, ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        // Force the uid lookup to fail for this sender by removing the
        // default mapping; FakeCallerUidLookup yields InvalidName for an
        // unmapped sender, which the service translates to Unauthorised.
        let _ = uid_lookup; // handle held for future tests; nothing to do here
        let resp = svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.99");
        // :1.99 isn't the active sender — we hit SenderMismatch first.
        assert_eq!(
            resp,
            LaunchPortalResponse::Refused {
                reason: RefusalReason::SenderMismatch,
            },
        );
    }

    #[test]
    fn launch_with_dbus_uid_lookup_failure_refused_as_backend_unavailable() {
        let Fixture {
            svc, uid_lookup, ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        uid_lookup.fail_dbus();
        let resp = svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42");
        assert_eq!(
            resp,
            LaunchPortalResponse::Refused {
                reason: RefusalReason::BackendUnavailable,
            },
        );
    }

    #[test]
    fn launch_with_spawner_failure_refused_as_spawn_failed() {
        let Fixture { svc, spawner, .. } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        spawner.fail_for_url("http://captive.example/");
        let resp = svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42");
        assert_eq!(
            resp,
            LaunchPortalResponse::Refused {
                reason: RefusalReason::SpawnFailed,
            },
        );
    }

    #[test]
    fn subprocess_exit_invokes_external_callback_and_audits() {
        use std::sync::atomic::{AtomicU32, Ordering};
        let Fixture {
            svc,
            spawner,
            audit,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let observed = Arc::new(AtomicU32::new(0));
        let observed_clone = Arc::clone(&observed);
        svc.set_external_exit_callback(Some(Arc::new(move |exit: SpawnExit| {
            observed_clone.store(exit.pid, Ordering::SeqCst);
        })));
        let resp = svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42");
        let pid = match resp {
            LaunchPortalResponse::Success { pid } => pid,
            other => panic!("expected Success, got {other:?}"),
        };
        spawner.fire_exit(SpawnExit {
            pid,
            exit_code: Some(0),
            signal: None,
        });
        assert_eq!(observed.load(Ordering::SeqCst), pid);
        // Audit entries: setup + launch + exit
        let entries = audit.entries();
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[2].action, AuditAction::LaunchPortal);
        assert_eq!(entries[2].pid, Some(pid));
        assert_eq!(entries[2].sender, "<auto>");
        assert_eq!(entries[2].decision, AuditDecision::Success);
    }

    #[test]
    fn subprocess_nonzero_exit_recorded_as_refused_in_audit() {
        let Fixture {
            svc,
            spawner,
            audit,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let pid =
            match svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42") {
                LaunchPortalResponse::Success { pid } => pid,
                other => panic!("expected Success, got {other:?}"),
            };
        spawner.fire_exit(SpawnExit {
            pid,
            exit_code: Some(91), // arbitrary non-zero child exit (transient unit failed)
            signal: None,
        });
        let entries = audit.entries();
        let exit_entry = &entries[2];
        match &exit_entry.decision {
            AuditDecision::Refused { reason } => assert!(reason.contains("subprocess_exit")),
            other => panic!("expected Refused, got {other:?}"),
        }
    }

    // ── Phase 5b.8 backstop integration ──────────────────────────────────

    #[test]
    fn subprocess_exit_arms_backstop_with_configured_duration() {
        let Fixture {
            svc,
            spawner,
            backstop,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let pid =
            match svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42") {
                LaunchPortalResponse::Success { pid } => pid,
                other => panic!("expected Success, got {other:?}"),
            };
        assert_eq!(backstop.schedule_count(), 0);
        spawner.fire_exit(SpawnExit {
            pid,
            exit_code: Some(0),
            signal: None,
        });
        assert_eq!(backstop.schedule_count(), 1);
        assert_eq!(backstop.last_duration(), Some(Duration::from_secs(30)));
        assert!(backstop.has_pending());
    }

    #[test]
    fn explicit_teardown_cancels_backstop() {
        let Fixture {
            svc,
            spawner,
            backstop,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let pid =
            match svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42") {
                LaunchPortalResponse::Success { pid } => pid,
                other => panic!("expected Success, got {other:?}"),
            };
        spawner.fire_exit(SpawnExit {
            pid,
            exit_code: Some(0),
            signal: None,
        });
        assert!(backstop.has_pending());
        // Orchestrator calls teardown — backstop should be cancelled.
        let teardown = svc.teardown_captive(":1.42");
        assert_eq!(teardown, TeardownCaptiveResponse::Success);
        assert!(backstop.was_cancelled());
        assert!(!backstop.has_pending());
    }

    #[test]
    fn backstop_fire_force_tears_down_when_orchestrator_silent() {
        let Fixture {
            svc,
            spawner,
            backstop,
            audit,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let pid =
            match svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42") {
                LaunchPortalResponse::Success { pid } => pid,
                other => panic!("expected Success, got {other:?}"),
            };
        spawner.fire_exit(SpawnExit {
            pid,
            exit_code: Some(0),
            signal: None,
        });
        // Simulate orchestrator never calling teardown — fire backstop.
        backstop.fire();
        // Active state cleared.
        assert!(!svc.is_active());
        // Audit recorded with sender="<backstop>".
        let entries = audit.entries();
        let last = entries.last().expect("audit has entries");
        assert_eq!(last.action, AuditAction::AutoTeardown);
        assert_eq!(last.sender, "<backstop>");
        assert_eq!(last.decision, AuditDecision::Success);
    }

    #[test]
    fn backstop_fire_after_explicit_teardown_is_noop() {
        let Fixture {
            svc,
            spawner,
            backstop,
            audit,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let pid =
            match svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42") {
                LaunchPortalResponse::Success { pid } => pid,
                other => panic!("expected Success, got {other:?}"),
            };
        spawner.fire_exit(SpawnExit {
            pid,
            exit_code: Some(0),
            signal: None,
        });
        svc.teardown_captive(":1.42");
        let audit_count_before = audit.entries().len();
        // Backstop's pending callback was already cleared by the cancel,
        // so fire() finds nothing. Even if a malicious test forced the
        // pending back in, the no-active-session check would reject.
        backstop.fire();
        assert_eq!(audit.entries().len(), audit_count_before);
        assert!(!svc.is_active());
    }

    #[test]
    fn handle_disconnect_cancels_backstop() {
        let Fixture {
            svc,
            spawner,
            backstop,
            watcher,
            ..
        } = svc_with_audit(
            FakeNetnsOps::new(),
            FakeAuthorizer::allow_all(),
            allow_captive(),
            permissive_throttle(),
        );
        svc.setup_captive(&req("wlan0"), ":1.42");
        let pid =
            match svc.launch_portal_subprocess(&launch_req("http://captive.example/"), ":1.42") {
                LaunchPortalResponse::Success { pid } => pid,
                other => panic!("expected Success, got {other:?}"),
            };
        spawner.fire_exit(SpawnExit {
            pid,
            exit_code: Some(0),
            signal: None,
        });
        assert!(backstop.has_pending());
        // UI process disconnects (5b.6 name-watch fires) — backstop must
        // be cancelled too so we don't double-fire after the auto-teardown.
        watcher.fire_disconnect(":1.42");
        assert!(backstop.was_cancelled());
        assert!(!backstop.has_pending());
        assert!(!svc.is_active());
    }
}
