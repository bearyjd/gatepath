"""Tests for the portal-observation channel (#123).

This carries security-relevant counts — including certificate-error bypasses —
from the portal subprocess into the audit log. Two properties matter:

* it must not be forgeable or misdirected (hence the path rules), and
* it must never break the session record when it fails (hence the tolerance).

Those pull in opposite directions, so both are pinned here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gatepath.portal_observations import (
    MAX_COUNT,
    collect_observations,
    PortalObservations,
    discard_observations,
    observations_path,
    read_observations,
    write_observations,
)


class TestObservationsPath:
    def test_path_is_keyed_by_pid_under_the_runtime_dir(self) -> None:
        p = observations_path("/run/user/1000", 4321)
        assert p == Path("/run/user/1000/gatepath/portal-observations-4321.json")

    def test_pid_keying_keeps_concurrent_sessions_apart(self) -> None:
        assert observations_path("/run/user/1000", 1) != observations_path(
            "/run/user/1000", 2
        )

    @pytest.mark.parametrize("runtime_dir", [None, "", "   "])
    def test_missing_runtime_dir_declines_rather_than_guessing(self, runtime_dir) -> None:
        """No XDG_RUNTIME_DIR must mean "don't write", not "use /tmp".

        /tmp is world-writable: another local user could pre-create or symlink
        the file and feed the audit log arbitrary counts. A missing file is
        already handled; a forgeable one would corrupt a security log.
        """
        assert observations_path(runtime_dir, 1234) is None

    def test_relative_runtime_dir_is_rejected(self) -> None:
        # Resolving against the process cwd would put a security artifact
        # somewhere unpredictable.
        assert observations_path("run/user/1000", 1234) is None

    @pytest.mark.parametrize("pid", [0, -1])
    def test_nonsense_pid_is_rejected(self, pid: int) -> None:
        assert observations_path("/run/user/1000", pid) is None


class TestRoundTrip:
    def test_write_then_read_preserves_counts(self, tmp_path: Path) -> None:
        path = tmp_path / "gatepath" / "portal-observations-7.json"
        obs = PortalObservations(
            off_domain_navigations=3, tracker_resources=11, tls_cert_errors_bypassed=1
        )
        assert write_observations(path, obs) is True
        assert read_observations(path) == obs

    def test_write_creates_the_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "deeper" / "obs.json"
        assert write_observations(path, PortalObservations()) is True
        assert path.exists()

    def test_write_to_none_path_is_a_declined_no_op(self) -> None:
        assert write_observations(None, PortalObservations(tracker_resources=5)) is False


class TestReadTolerance:
    """A failed read must cost the counts, never the session record."""

    def test_missing_file_reads_as_none(self, tmp_path: Path) -> None:
        assert read_observations(tmp_path / "absent.json") is None

    def test_none_path_reads_as_none(self) -> None:
        assert read_observations(None) is None

    def test_corrupt_json_reads_as_none(self, tmp_path: Path) -> None:
        p = tmp_path / "obs.json"
        p.write_text("{not json", encoding="utf-8")
        assert read_observations(p) is None

    def test_truncated_write_reads_as_none(self, tmp_path: Path) -> None:
        p = tmp_path / "obs.json"
        p.write_text('{"off_domain_navigations": 3', encoding="utf-8")
        assert read_observations(p) is None

    @pytest.mark.parametrize("payload", ["[]", '"a string"', "42", "null"])
    def test_non_object_payload_reads_as_none(self, tmp_path: Path, payload: str) -> None:
        p = tmp_path / "obs.json"
        p.write_text(payload, encoding="utf-8")
        assert read_observations(p) is None

    def test_absent_keys_default_to_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "obs.json"
        p.write_text(json.dumps({"off_domain_navigations": 2}), encoding="utf-8")
        assert read_observations(p) == PortalObservations(off_domain_navigations=2)


class TestValueClamping:
    """Values come off disk, so they are input, not data we produced."""

    @pytest.mark.parametrize(
        "value", [-1, -999, "3", None, 1.5, [], {}, True, False]
    )
    def test_non_count_values_become_zero(self, tmp_path: Path, value) -> None:
        p = tmp_path / "obs.json"
        p.write_text(json.dumps({"tls_cert_errors_bypassed": value}), encoding="utf-8")
        got = read_observations(p)
        assert got is not None
        assert got.tls_cert_errors_bypassed == 0

    def test_absurd_counts_are_capped(self, tmp_path: Path) -> None:
        p = tmp_path / "obs.json"
        p.write_text(
            json.dumps({"tracker_resources": MAX_COUNT * 1000}), encoding="utf-8"
        )
        got = read_observations(p)
        assert got is not None
        assert got.tracker_resources == MAX_COUNT


class TestDiscard:
    def test_discard_removes_the_file(self, tmp_path: Path) -> None:
        p = tmp_path / "obs.json"
        write_observations(p, PortalObservations())
        discard_observations(p)
        assert not p.exists()

    def test_discard_is_safe_when_already_gone(self, tmp_path: Path) -> None:
        discard_observations(tmp_path / "never-existed.json")

    def test_discard_of_none_is_safe(self) -> None:
        discard_observations(None)


class TestCollectObservations:
    """The consume-once seam window.py uses."""

    def test_collect_returns_counts_and_removes_the_file(self, tmp_path: Path) -> None:
        pid = 4242
        path = observations_path(str(tmp_path), pid)
        assert path is not None
        write_observations(path, PortalObservations(tls_cert_errors_bypassed=1))

        got = collect_observations(str(tmp_path), pid)
        assert got == PortalObservations(tls_cert_errors_bypassed=1)
        assert not path.exists(), "the file must not survive to be read twice"

    def test_second_collect_yields_nothing(self, tmp_path: Path) -> None:
        """PIDs are reused; a leftover file would leak one session's counts
        into another session's audit entry."""
        pid = 4242
        write_observations(observations_path(str(tmp_path), pid), PortalObservations(tracker_resources=7))
        assert collect_observations(str(tmp_path), pid) is not None
        assert collect_observations(str(tmp_path), pid) is None

    def test_collect_without_a_runtime_dir_is_none(self) -> None:
        assert collect_observations(None, 1234) is None
