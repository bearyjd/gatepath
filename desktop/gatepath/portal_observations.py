"""Carries the portal WebView's observation counts back to the audit writer.

The portal WebView runs in a subprocess (`portal_webview_runner`, spawned by
the netns helper). Its counters — off-domain navigations, tracker subresources,
certificate-error bypasses — used to reach one log line at exit and die there,
so every one of those fields was written to the audit log as 0 no matter what
happened. See issue #123.

This is the channel. The runner writes a small JSON file keyed by its own PID;
the app reads it when the subprocess exits, applies the counts to the session,
and then writes the audit entry.

**Why a file, and why this file.** The helper already sets
`XDG_RUNTIME_DIR=/run/user/<uid>` for the subprocess, derived server-side from
the authenticated caller UID rather than taken from the client — so no new
D-Bus argument, no new validator, and no change to the spawn contract. That
directory is `0700` and user-owned, which matters: a world-writable path like
`/tmp` would let another local user pre-create or symlink the file and feed the
audit log whatever counts they liked.

If `XDG_RUNTIME_DIR` is absent we **decline to write** rather than falling back
to a less safe location. A missing observation file is already handled — the
counts stay 0, exactly as before this module existed — whereas a forgeable one
would quietly corrupt a security log.

Pure stdlib; no `gi`, so both sides can be tested headlessly.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DIR_NAME = "gatepath"
_FILE_PREFIX = "portal-observations-"

#: Refuse absurd values rather than writing them into an audit entry. A counter
#: this large means a bug or a tampered file, not a real session.
MAX_COUNT = 1_000_000


@dataclass(frozen=True)
class PortalObservations:
    """What the portal WebView saw during one session."""

    off_domain_navigations: int = 0
    tracker_resources: int = 0
    tls_cert_errors_bypassed: int = 0


def observations_path(runtime_dir: Optional[str], pid: int) -> Optional[Path]:
    """Where the runner with `pid` writes its counts, or None if unavailable.

    Keyed by PID so a stale file from an earlier session can never be read as
    this one's, and so two concurrent sessions cannot overwrite each other.
    """
    if not runtime_dir or not runtime_dir.strip():
        return None
    base = Path(runtime_dir.strip())
    if not base.is_absolute():
        # A relative XDG_RUNTIME_DIR is malformed; resolving it against the
        # process cwd would put a security artifact somewhere unpredictable.
        return None
    if pid <= 0:
        return None
    return base / _DIR_NAME / f"{_FILE_PREFIX}{pid}.json"


def _clamp(value: object) -> int:
    """Coerce a value from disk into a sane count. Never raises."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if value < 0:
        return 0
    return min(value, MAX_COUNT)


def write_observations(path: Optional[Path], observations: PortalObservations) -> bool:
    """Write `observations` atomically. Best-effort — never raises.

    Called from the runner's exit path, where an exception would turn a
    bookkeeping problem into a crash the user sees.
    """
    if path is None:
        logger.warning(
            "no XDG_RUNTIME_DIR; portal observation counts will not reach the audit log"
        )
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "off_domain_navigations": observations.off_domain_navigations,
            "tracker_resources": observations.tracker_resources,
            "tls_cert_errors_bypassed": observations.tls_cert_errors_bypassed,
        }
        # Atomic: a reader either sees the previous state or the complete new
        # one, never a half-written file.
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".obs-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp_name, path)
        except Exception:
            os.unlink(tmp_name)
            raise
        return True
    except Exception as exc:  # noqa: BLE001 — bookkeeping must not break exit
        logger.warning("could not write portal observations to %s: %s", path, exc)
        return False


def read_observations(path: Optional[Path]) -> Optional[PortalObservations]:
    """Read counts written by the runner, or None if unavailable/unusable.

    Tolerant by design: a missing, empty, corrupt or wrong-shaped file yields
    None, which leaves the audit entry's counters at 0 — the pre-existing
    behaviour. Refusing to write an entry at all because bookkeeping failed
    would lose the session record entirely, which is worse.
    """
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("unreadable portal observations at %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("portal observations at %s is not an object", path)
        return None
    return PortalObservations(
        off_domain_navigations=_clamp(raw.get("off_domain_navigations")),
        tracker_resources=_clamp(raw.get("tracker_resources")),
        tls_cert_errors_bypassed=_clamp(raw.get("tls_cert_errors_bypassed")),
    )


def discard_observations(path: Optional[Path]) -> None:
    """Remove the file once consumed. Best-effort — never raises."""
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not remove portal observations at %s: %s", path, exc)


def collect_observations(
    runtime_dir: Optional[str], pid: int
) -> Optional[PortalObservations]:
    """Read the runner's counts and remove the file, in one step.

    The consume-once shape is deliberate: the file is keyed by PID, and PIDs
    are reused. Leaving it behind would let a later session with the same PID
    read a previous session's counts into its audit entry.

    Extracted from `window.py` so the logic is testable without GTK — the same
    split `isolation_should_engage` uses.
    """
    path = observations_path(runtime_dir, pid)
    observations = read_observations(path)
    discard_observations(path)
    return observations

