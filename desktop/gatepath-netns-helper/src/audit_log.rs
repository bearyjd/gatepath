//! Per-action audit log for the helper.
//!
//! Every privileged operation (`SetupCaptive`, `TeardownCaptive`) emits one
//! line of JSON describing what was asked, by whom, and what the helper
//! decided. The file lives at `/var/lib/gatepath/helper-audit.jsonl` per
//! the systemd unit's `StateDirectory=gatepath` setting.
//!
//! This is the helper's OWN audit log — distinct from the per-session
//! audit log Android writes (`docs/audit_log_schema.json`). Cross-platform
//! parity isn't a goal here; the helper's log is per-action, with helper-
//! specific fields like the calling D-Bus sender. Combining them would
//! conflate two different abstractions.
//!
//! Trait abstraction so unit tests can capture entries without a real file.

use std::fs::OpenOptions;
use std::io::{BufWriter, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::Serialize;
use thiserror::Error;

/// File mode for the audit log. `0640` = owner rw, group r, world none.
/// Helper runs as root with `StateDirectory=gatepath` (`0750` per the
/// systemd unit), so the audit log is reachable by root and the gatepath
/// group only — not world-readable. Closes a temporal-pattern leak to
/// other local users that the default umask-derived `0644` would expose.
///
/// `mode()` only applies on file CREATION; if the file already exists
/// with looser permissions, this does NOT tighten them. Operators should
/// ensure the systemd unit creates the StateDirectory fresh.
const AUDIT_LOG_MODE: u32 = 0o640;

/// One privileged-operation event. Schema is internal to the helper —
/// changing field names is a breaking change for any external log
/// consumer, but we don't ship one in 5b.5.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuditEntry {
    pub timestamp_utc: String,
    pub action: AuditAction,
    pub sender: String,
    /// Present for setup; absent for teardown.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub interface: Option<String>,
    pub decision: AuditDecision,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AuditAction {
    SetupCaptive,
    TeardownCaptive,
}

/// Decision recorded in the audit log. `Refused` carries the
/// [`crate::RefusalReason`] variant name as a string so the log is
/// readable without reference to crate internals.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AuditDecision {
    Success,
    Refused { reason: String },
}

#[derive(Debug, Error)]
pub enum AuditError {
    #[error("could not open audit log {path}: {source}")]
    OpenFailed {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
}

/// Writes audit entries somewhere durable. Real impl is
/// [`FileAuditWriter`] (JSONL on disk); tests use [`FakeAuditWriter`].
pub trait AuditWriter: Send + Sync {
    fn append(&self, entry: &AuditEntry);
}

// ── Production impl ────────────────────────────────────────────────────

pub struct FileAuditWriter {
    file: Mutex<BufWriter<std::fs::File>>,
    path: PathBuf,
}

impl FileAuditWriter {
    /// Open or create the audit log at `path`. Append-only — never truncates.
    ///
    /// # Errors
    ///
    /// Returns [`AuditError::OpenFailed`] if the path can't be opened for
    /// append. Common causes: directory missing (systemd's StateDirectory
    /// should have created it), insufficient permissions.
    pub fn open(path: impl Into<PathBuf>) -> Result<Self, AuditError> {
        let path = path.into();
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .mode(AUDIT_LOG_MODE)
            .open(&path)
            .map_err(|source| AuditError::OpenFailed {
                path: path.clone(),
                source,
            })?;
        Ok(Self {
            file: Mutex::new(BufWriter::new(file)),
            path,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl AuditWriter for FileAuditWriter {
    fn append(&self, entry: &AuditEntry) {
        let line = match serde_json::to_string(entry) {
            Ok(s) => s,
            Err(_) => {
                // Serialisation should never fail for our struct shapes;
                // if it does, drop the entry rather than crash the helper.
                tracing::error!("audit serialise failed (dropped entry)");
                return;
            }
        };
        let mut writer = self.file.lock().expect("audit file mutex poisoned");
        // We deliberately ignore I/O errors here — losing audit lines is
        // bad but crashing the helper is worse. Surface via tracing only.
        if let Err(e) = writeln!(writer, "{line}") {
            tracing::error!(error = %e, "audit append failed");
        }
        if let Err(e) = writer.flush() {
            tracing::error!(error = %e, "audit flush failed");
        }
    }
}

/// Helper for the orchestrator: build an entry with the current UTC
/// timestamp without forcing every call site to import chrono.
pub fn entry_now(
    action: AuditAction,
    sender: impl Into<String>,
    interface: Option<String>,
    decision: AuditDecision,
) -> AuditEntry {
    AuditEntry {
        timestamp_utc: chrono::Utc::now().to_rfc3339(),
        action,
        sender: sender.into(),
        interface,
        decision,
    }
}

// ── Fake impl for tests ────────────────────────────────────────────────

#[cfg(test)]
pub struct FakeAuditWriter {
    pub entries: Mutex<Vec<AuditEntry>>,
}

#[cfg(test)]
impl FakeAuditWriter {
    pub fn new() -> Self {
        Self {
            entries: Mutex::new(Vec::new()),
        }
    }

    pub fn entries(&self) -> Vec<AuditEntry> {
        self.entries.lock().unwrap().clone()
    }
}

#[cfg(test)]
impl Default for FakeAuditWriter {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
impl AuditWriter for FakeAuditWriter {
    fn append(&self, entry: &AuditEntry) {
        self.entries.lock().unwrap().push(entry.clone());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader};

    fn sample_entry() -> AuditEntry {
        AuditEntry {
            timestamp_utc: "2026-05-09T10:30:00Z".into(),
            action: AuditAction::SetupCaptive,
            sender: ":1.42".into(),
            interface: Some("wlan0".into()),
            decision: AuditDecision::Success,
        }
    }

    #[test]
    fn fake_captures_appended_entries() {
        let w = FakeAuditWriter::new();
        w.append(&sample_entry());
        w.append(&AuditEntry {
            decision: AuditDecision::Refused {
                reason: "not_captive".into(),
            },
            ..sample_entry()
        });
        let captured = w.entries();
        assert_eq!(captured.len(), 2);
        assert_eq!(captured[0].decision, AuditDecision::Success);
        assert!(matches!(
            captured[1].decision,
            AuditDecision::Refused { .. }
        ));
    }

    #[test]
    fn file_writer_emits_one_json_line_per_entry() {
        let dir = tempfile_dir();
        let path = dir.join("test-audit.jsonl");
        let writer = FileAuditWriter::open(&path).expect("open");
        writer.append(&sample_entry());
        writer.append(&AuditEntry {
            action: AuditAction::TeardownCaptive,
            interface: None,
            ..sample_entry()
        });
        drop(writer); // flush + close

        let f = std::fs::File::open(&path).expect("reopen");
        let lines: Vec<String> = BufReader::new(f).lines().map(|l| l.unwrap()).collect();
        assert_eq!(lines.len(), 2);
        // Each line must be valid JSON.
        for line in &lines {
            let _: serde_json::Value = serde_json::from_str(line).expect("valid JSON line");
        }
        // Snake-case enum encoding pinned.
        assert!(lines[0].contains("\"action\":\"setup_captive\""));
        assert!(lines[1].contains("\"action\":\"teardown_captive\""));
        assert!(lines[0].contains("\"interface\":\"wlan0\""));
        // Teardown entry should omit `interface` field entirely.
        assert!(
            !lines[1].contains("interface"),
            "teardown entry leaked interface field: {}",
            lines[1]
        );
    }

    #[test]
    fn refused_decision_includes_reason_string() {
        let entry = AuditEntry {
            decision: AuditDecision::Refused {
                reason: "throttled".into(),
            },
            ..sample_entry()
        };
        let json = serde_json::to_string(&entry).unwrap();
        assert!(json.contains("\"kind\":\"refused\""));
        assert!(json.contains("\"reason\":\"throttled\""));
    }

    #[test]
    fn entry_now_uses_current_utc() {
        let entry = entry_now(
            AuditAction::SetupCaptive,
            ":1.99",
            Some("wlan0".into()),
            AuditDecision::Success,
        );
        // Should parse as RFC 3339.
        let parsed = chrono::DateTime::parse_from_rfc3339(&entry.timestamp_utc);
        assert!(
            parsed.is_ok(),
            "invalid RFC 3339 timestamp: {}",
            entry.timestamp_utc,
        );
    }

    fn tempfile_dir() -> PathBuf {
        let mut dir = std::env::temp_dir();
        dir.push(format!("gatepath-audit-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("mkdir tempdir");
        dir
    }
}
