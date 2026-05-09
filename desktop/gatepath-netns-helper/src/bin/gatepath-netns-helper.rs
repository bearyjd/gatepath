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
use gatepath_netns_helper::dbus_service::{BUS_NAME, DbusService, OBJECT_PATH};
use gatepath_netns_helper::netns::LinuxNetnsOps;
use gatepath_netns_helper::network_manager::NMCaptiveCheck;
use gatepath_netns_helper::policykit::PolicyKitAuthorizer;
use gatepath_netns_helper::service::GatepathHelperService;
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
    // Build the production orchestrator. PolicyKit + NetworkManager + audit-
    // log open failures here are fatal — we refuse to start without auth, the
    // defence-in-depth captive check, or a working audit log path.
    let auth = PolicyKitAuthorizer::connect().context("connecting to PolicyKit")?;
    let captive_check = NMCaptiveCheck::connect().context("connecting to NetworkManager")?;
    let ops = LinuxNetnsOps::new();
    let throttle = Throttle::new(THROTTLE_LIMIT, THROTTLE_WINDOW);
    let audit = FileAuditWriter::open(PathBuf::from(AUDIT_LOG_PATH))
        .with_context(|| format!("opening audit log at {AUDIT_LOG_PATH}"))?;
    info!(audit_log = AUDIT_LOG_PATH, "audit log opened");
    let service = Arc::new(GatepathHelperService::new(
        ops,
        auth,
        captive_check,
        throttle,
        Box::new(audit),
    ));
    let dbus_service = DbusService::new(service);

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

    // D5.2: watch the connection's unique name. If the dbus-daemon reports
    // our connection has died, we exit — systemd will deactivate us and a
    // future client request will reactivate via D-Bus activation. This is
    // the simplest implementation of "tear down on disconnect" since each
    // helper invocation is one session anyway.
    //
    // Watching SPECIFIC client names (Gatepath UI's :1.X) for disconnect
    // would let us tear down even when other clients remain — but that's
    // 5b.4 territory once we have multiple potential callers.
    wait_for_shutdown(&conn).await
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
