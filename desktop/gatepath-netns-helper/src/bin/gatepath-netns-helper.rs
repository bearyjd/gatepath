//! `gatepath-netns-helper` — privileged D-Bus daemon.
//!
//! Wires production components together:
//!
//!   - [`LinuxNetnsOps`] — runs `ip` commands as the privileged kernel surface
//!   - [`PolicyKitAuthorizer`] — checks `org.freedesktop.PolicyKit1.Authority`
//!     before each method call
//!   - [`GatepathHelperService`] — orchestrates validation + auth + ops
//!   - [`DbusService`] — exposes the orchestrator on the system bus
//!
//! After registration the daemon idles until SIGTERM (from systemd) or
//! D-Bus client disconnect (which we treat as "tear down whatever was
//! active and exit", per D5.2). Logs go to stderr — systemd's journal
//! handler picks them up.

use std::process::ExitCode;
use std::sync::Arc;

use std::path::PathBuf;
use std::time::Duration;

use anyhow::Context;
use gatepath_netns_helper::audit_log::FileAuditWriter;
use gatepath_netns_helper::caller_uid::DbusCallerUidLookup;
use gatepath_netns_helper::connectivity::LinuxNetnsConnectivity;
use gatepath_netns_helper::dbus_service::{BUS_NAME, DbusService, OBJECT_PATH};
use gatepath_netns_helper::name_watch::LinuxNameWatcher;
use gatepath_netns_helper::netns::LinuxNetnsOps;
use gatepath_netns_helper::network_manager::NMCaptiveCheck;
use gatepath_netns_helper::policykit::PolicyKitAuthorizer;
use gatepath_netns_helper::service::{BackstopConfig, Deps, GatepathHelperService};
use gatepath_netns_helper::spawn::{LinuxSpawner, PORTAL_RUNNER_PATH};
use gatepath_netns_helper::throttle::Throttle;
use tokio::signal::unix::{SignalKind, signal};
use tracing::{error, info, warn};
use zbus::connection;

/// Per-sender rate limit. Prevents prompt-fatigue DoS: 5 SetupCaptive calls
/// per 60s from the same sender. Real Gatepath UI never approaches this.
const THROTTLE_LIMIT: usize = 5;
const THROTTLE_WINDOW: Duration = Duration::from_secs(60);

/// Audit log path. Matches `StateDirectory=gatepath` in the systemd unit:
/// systemd creates `/var/lib/gatepath/` with the helper's UID at startup.
const AUDIT_LOG_PATH: &str = "/var/lib/gatepath/helper-audit.jsonl";

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> ExitCode {
    init_tracing();
    info!("gatepath-netns-helper starting");

    match run().await {
        Ok(()) => {
            info!("gatepath-netns-helper exiting cleanly");
            ExitCode::SUCCESS
        }
        Err(e) => {
            error!(error = %e, "fatal error");
            ExitCode::FAILURE
        }
    }
}

fn init_tracing() {
    use tracing_subscriber::EnvFilter;
    let filter = EnvFilter::try_from_env("GATEPATH_LOG").unwrap_or_else(|_| EnvFilter::new("info"));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(false)
        .with_writer(std::io::stderr)
        .init();
}

async fn run() -> anyhow::Result<()> {
    // Build the production orchestrator. PolicyKit + NetworkManager +
    // name-watcher + spawner + caller-uid + audit-log open failures here
    // are fatal — we refuse to start without auth, the defence-in-depth
    // captive check, the disconnect watch (5b.6), the privileged spawn
    // capability (5b.7), or a working audit log path.
    //
    // The four `connect()` calls construct `zbus::blocking::Connection`s,
    // which internally `block_on` an async future. With zbus's `tokio`
    // feature enabled, that path tries to start a tokio runtime — and
    // since we're already inside `#[tokio::main]`'s multi-thread runtime,
    // `Runtime::new()` panics with "Cannot start a runtime from within
    // a runtime". Move the blocking init to a dedicated worker thread
    // so the inner `block_on` runs on a clean stack with no ambient
    // tokio runtime. The Rust unit suite never caught this because the
    // production `connect()` paths are replaced with fakes; the bug only
    // surfaces when the daemon actually starts.
    let (auth, captive_check, watcher, caller_uid_lookup) =
        tokio::task::spawn_blocking(|| -> anyhow::Result<_> {
            let auth = PolicyKitAuthorizer::connect().context("connecting to PolicyKit")?;
            let captive_check =
                NMCaptiveCheck::connect().context("connecting to NetworkManager")?;
            let watcher =
                LinuxNameWatcher::connect().context("connecting to D-Bus for name watch")?;
            let caller_uid_lookup =
                DbusCallerUidLookup::connect().context("connecting to D-Bus for UID lookup")?;
            Ok((auth, captive_check, watcher, caller_uid_lookup))
        })
        .await
        .context("spawn_blocking for blocking-zbus init panicked")??;
    if !std::path::Path::new(PORTAL_RUNNER_PATH).exists() {
        anyhow::bail!(
            "portal runner not installed at {PORTAL_RUNNER_PATH}; \
             refusing to start without spawn target"
        );
    }
    let spawner = LinuxSpawner::new(PORTAL_RUNNER_PATH);
    let ops = LinuxNetnsOps::new();
    let connectivity = LinuxNetnsConnectivity::new();
    let throttle = Throttle::new(THROTTLE_LIMIT, THROTTLE_WINDOW);
    let audit = FileAuditWriter::open(PathBuf::from(AUDIT_LOG_PATH))
        .with_context(|| format!("opening audit log at {AUDIT_LOG_PATH}"))?;
    info!(audit_log = AUDIT_LOG_PATH, "audit log opened");
    let service = Arc::new(GatepathHelperService::new(Deps {
        ops,
        auth,
        captive_check,
        throttle,
        watcher,
        spawner: Box::new(spawner),
        caller_uid_lookup: Box::new(caller_uid_lookup),
        connectivity: Box::new(connectivity),
        backstop: BackstopConfig::production(),
        audit: Box::new(audit),
    }));
    let dbus_service = DbusService::new(Arc::clone(&service));

    let conn = connection::Builder::system()
        .context("system bus builder")?
        .name(BUS_NAME)
        .context("requesting bus name")?
        .serve_at(OBJECT_PATH, dbus_service)
        .context("registering at object path")?
        .build()
        .await
        .context("building D-Bus connection")?;

    info!(
        bus_name = BUS_NAME,
        object_path = OBJECT_PATH,
        "registered on system bus"
    );

    // Bridge: subprocess exit (delivered on the spawner's wait-thread) →
    // D-Bus PortalSubprocessExited signal (emitted on the tokio reactor).
    // The wait-thread can't be async; we route via an unbounded channel.
    let (exit_tx, mut exit_rx) =
        tokio::sync::mpsc::unbounded_channel::<gatepath_netns_helper::spawn::SpawnExit>();
    let conn_for_signal = conn.clone();
    tokio::spawn(async move {
        while let Some(exit) = exit_rx.recv().await {
            let exit_code = exit.exit_code.unwrap_or(-1);
            let signal_num = exit.signal.unwrap_or(0);
            let object_server = conn_for_signal.object_server();
            let iface_ref =
                match object_server
                    .interface::<_, DbusService<
                        LinuxNetnsOps,
                        PolicyKitAuthorizer,
                        NMCaptiveCheck,
                        LinuxNameWatcher,
                    >>(OBJECT_PATH)
                    .await
                {
                    Ok(r) => r,
                    Err(e) => {
                        warn!(error = %e, "interface ref unavailable; dropping signal");
                        continue;
                    }
                };
            if let Err(e) = DbusService::<
                LinuxNetnsOps,
                PolicyKitAuthorizer,
                NMCaptiveCheck,
                LinuxNameWatcher,
            >::portal_subprocess_exited(
                iface_ref.signal_emitter(), exit.pid, exit_code, signal_num
            )
            .await
            {
                warn!(error = %e, "PortalSubprocessExited emit failed");
            }
        }
    });

    let exit_tx_for_cb = exit_tx.clone();
    service.set_external_exit_callback(Some(std::sync::Arc::new(
        move |exit: gatepath_netns_helper::spawn::SpawnExit| {
            let _ = exit_tx_for_cb.send(exit);
        },
    )));

    wait_for_shutdown(&conn).await?;

    // Shutdown: tear down any still-active session (a SIGTERM / systemd stop
    // with a live session would otherwise leak the netns), then exit the
    // process directly. We deliberately do NOT fall through to a normal return:
    // unwinding `#[tokio::main]`'s runtime drops the blocking-zbus connections
    // (PolicyKit / NetworkManager / name-watch / UID-lookup), whose `Drop`
    // calls `block_on`, which panics on a tokio worker ("Cannot start a runtime
    // from within a runtime"). `process::exit` skips those Drops; the netns is
    // already torn down here and the audit log flushes on every append.
    let svc = Arc::clone(&service);
    let _ = tokio::task::spawn_blocking(move || svc.shutdown_teardown()).await;
    std::process::exit(0)
}

async fn wait_for_shutdown(_conn: &zbus::Connection) -> anyhow::Result<()> {
    let mut sigterm = signal(SignalKind::terminate()).context("install SIGTERM handler")?;
    let mut sigint = signal(SignalKind::interrupt()).context("install SIGINT handler")?;

    tokio::select! {
        _ = sigterm.recv() => warn!("SIGTERM received, shutting down"),
        _ = sigint.recv() => warn!("SIGINT received, shutting down"),
    }
    Ok(())
}
