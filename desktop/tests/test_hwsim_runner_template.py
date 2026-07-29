"""The hwsim runner template must always produce a parseable verdict.

That verdict is the harness's only oracle: `run.sh` reads `sentinel_reachable`
out of it to decide whether the no-leak invariant held. If the runner dies
before writing it, the gate cannot be evaluated at all and the run fails with
"runner never wrote a verdict" — which is what happened when a `$LOG` typo in
the optional WebView probe aborted the script under `set -u`, taking the
mandatory verdict with it.

These tests render the template exactly as `run.sh` does and **execute it**.
`bash -n` cannot catch an unbound variable — that is a runtime failure — so
syntax checking would not have caught the bug this file exists to prevent.

Lives in the desktop suite because that is where the Python test runner is;
it reaches across to `tests/e2e-hwsim/`, the same way `test_cause_parity.py`
reads the sibling `android/` tree.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE = REPO_ROOT / "tests" / "e2e-hwsim" / "portal-webview-runner.hwsim"

# Port 9 (discard) refuses fast, so the in-script curls fail immediately
# instead of burning their -m 4 / -m 6 timeouts.
DEAD_URL = "http://127.0.0.1:9"


#: The real runner execs a WebKit GUI that runs until the window closes, so a
#: test can never let it start. We stub the exec — but assert the line is
#: present first, so a stub can't silently mask its removal.
EXEC_LINE = "exec /usr/bin/python3 -m gatepath.portal_webview_runner"

#: The runner's own words when the import probe fails.
#:
#: Match on THIS, never on the bare `import gatepath.portal_webview_runner`
#: command: the template runs under `set -x`, so the probe's command line is
#: traced into the log whether it succeeds or fails. Asserting on the command
#: therefore passes in both cases and pins nothing — the same vacuous-assertion
#: shape this file was rewritten to remove. test_a_successful_probe_does_not_
#: log_the_failure_message is the negative control that keeps it that way.
PROBE_FAILURE_MESSAGE = (
    "webview requested but 'import gatepath.portal_webview_runner' failed"
)


def _render(tmp_path: Path, *, marker: Path, pythonpath: str | None = None) -> Path:
    """Substitute the @TOKEN@ placeholders exactly as run.sh's sed does."""
    text = TEMPLATE.read_text(encoding="utf-8")
    subs = {
        "@IFACE@": "lo",
        "@CLIENT_CIDR@": "127.0.0.2/8",
        "@GATEWAY@": "127.0.0.1",
        "@SENTINEL_URL@": f"{DEAD_URL}/health",
        "@VERDICT@": str(tmp_path / "verdict.json"),
        "@WEBVIEW_MARKER@": str(marker),
        "@GATEPATH_PYTHONPATH@": pythonpath if pythonpath is not None else str(REPO_ROOT / "desktop"),
        # Per-test, NOT the real /tmp/gatepath-hwsim-runner.log: the runner
        # appends, so a shared path would both accumulate across tests and
        # clobber the artifact a real hardware run left behind.
        "@RUNNER_LOG@": str(tmp_path / "runner.log"),
    }
    for token, value in subs.items():
        text = text.replace(token, value)
    # Exactly the check run.sh makes before installing the runner — a token
    # added anywhere in the file must not slip through unsubstituted.
    leftover = re.findall(r"@[A-Z_][A-Z_]*@", text)
    assert not leftover, (
        f"unsubstituted placeholder(s) in the rendered runner: {sorted(set(leftover))}"
    )
    assert EXEC_LINE in text, (
        "the runner no longer execs the real WebView — these tests stub that "
        "line, so its removal would otherwise go unnoticed"
    )
    # Replace the WHOLE line: a substring swap plus a trailing `#` would
    # comment out something unintended if the exec is ever reformatted.
    text = re.sub(
        rf"^\s*{re.escape(EXEC_LINE)}.*$",
        '    echo "STUBBED EXEC" >&2; exit 0',
        text,
        flags=re.M,
    )

    rendered = tmp_path / "runner.sh"
    rendered.write_text(text, encoding="utf-8")
    rendered.chmod(0o755)
    return rendered


def _run(rendered: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(rendered), f"{DEAD_URL}/portal"],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _log(rendered: Path) -> str:
    """Everything the runner prints, read from its self-log.

    The runner's first act is `exec >>"$RUNNER_LOG" 2>&1`, so the subprocess's
    own stdout/stderr are ALWAYS empty — asserting on `result.stderr` here is
    vacuously true and pins nothing. `@RUNNER_LOG@` is rendered per-test, so
    this is the only place the runner's output can be observed.
    """
    log = rendered.parent / "runner.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


@pytest.fixture(autouse=True)
def _needs_bash():
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("bash not available")


def test_template_has_no_unsubstituted_tokens_after_render(tmp_path: Path) -> None:
    rendered = _render(tmp_path, marker=tmp_path / "absent.marker")
    assert "@VERDICT@" not in rendered.read_text(encoding="utf-8")


def test_verdict_is_written_without_the_webview_marker(tmp_path: Path) -> None:
    """The default (headless) path — this is the no-leak gate's oracle."""
    rendered = _render(tmp_path, marker=tmp_path / "absent.marker")
    _run(rendered)
    verdict = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["webview_requested"] is False
    assert verdict["webview_ok"] is False


def test_verdict_is_written_WITH_the_webview_marker(tmp_path: Path) -> None:
    """The regression.

    A `$LOG` reference in this branch aborted the script under `set -u` before
    the verdict `printf`, and the `|| true` on the enclosing block did not
    catch it — the shell exits outright. The verdict never appeared and the
    no-leak gate could not be evaluated.
    """
    marker = tmp_path / "webview.enabled"
    marker.touch()
    rendered = _render(tmp_path, marker=marker)
    _run(rendered)

    verdict_path = tmp_path / "verdict.json"
    assert verdict_path.exists(), (
        "no verdict written with the webview marker set — the optional probe "
        f"aborted the runner again. runner log tail:\n{_log(rendered)[-2000:]}"
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert verdict["webview_requested"] is True


def test_no_unbound_variable_errors_on_either_path(tmp_path: Path) -> None:
    """`set -u` failures are what killed the verdict; catch them directly."""
    for marker_present in (False, True):
        marker = tmp_path / f"m{int(marker_present)}.enabled"
        if marker_present:
            marker.touch()
        sub = tmp_path / f"case{int(marker_present)}"
        sub.mkdir()
        rendered = _render(sub, marker=marker)
        _run(rendered)
        log = _log(rendered)
        assert log, (
            "the runner produced no self-log at all — it died before its "
            "`exec >>` redirect, or the log is no longer rendered here"
        )
        assert "unbound variable" not in log, (
            f"unbound variable with marker_present={marker_present}:\n"
            f"{log[-1000:]}"
        )


def test_verdict_stays_valid_json_when_the_portal_is_unreachable(
    tmp_path: Path,
) -> None:
    """curl emits `000` when it cannot connect.

    Unquoted, `"portal_http_code":000` is invalid JSON (leading zeros), so the
    verdict became unparseable exactly in the failure case run.sh needs to
    diagnose — `jq -r` on *any* field then returned nothing.
    """
    marker = tmp_path / "webview.enabled"
    marker.touch()
    rendered = _render(tmp_path, marker=marker)
    _run(rendered)

    raw = (tmp_path / "verdict.json").read_text(encoding="utf-8")
    verdict = json.loads(raw)  # the assertion: it parses at all
    # The portal was unreachable by construction, so this is the failure shape.
    assert verdict["portal_curl_rc"] != 0
    assert isinstance(verdict["portal_http_code"], str)


def test_verdict_carries_every_field_run_sh_reads(tmp_path: Path) -> None:
    """Drift guard: run.sh jq-reads these; a rename would silently yield null."""
    marker = tmp_path / "webview.enabled"
    marker.touch()
    rendered = _render(tmp_path, marker=marker)
    _run(rendered)
    verdict = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    for field in (
        "sentinel_reachable",
        "portal_http_code",
        "portal_curl_rc",
        "webview_ok",
    ):
        assert field in verdict, f"run.sh reads .{field}; the runner stopped emitting it"


def test_webview_ok_is_true_when_the_package_is_importable(tmp_path: Path) -> None:
    """The #131 regression, pinned.

    GATEPATH_PYTHONPATH was never rendered, so root's python could not import
    the package, the exec died on ModuleNotFoundError, and the harness still
    reported PASS. Every other test here passes with that bug present — they
    check `webview_requested`, which is true either way — so this is the one
    that would catch it coming back.
    """
    marker = tmp_path / "webview.enabled"
    marker.touch()
    _run(_render(tmp_path, marker=marker))
    verdict = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["webview_ok"] is True, (
        "import probe failed with GATEPATH_PYTHONPATH rendered — #131 has regressed"
    )


def test_a_successful_probe_does_not_log_the_failure_message(tmp_path: Path) -> None:
    """Negative control for PROBE_FAILURE_MESSAGE.

    Without this, the degradation test could go back to matching a needle that
    is present on BOTH paths and nobody would notice. The template runs under
    `set -x`, so the probe's own command line IS traced into the log on success
    — which is exactly how the first version of that assertion managed to pass
    while pinning nothing. Anything the degradation test matches on must be
    absent here.
    """
    marker = tmp_path / "webview.enabled"
    marker.touch()
    rendered = _render(tmp_path, marker=marker)  # importable: the probe succeeds
    _run(rendered)

    verdict = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["webview_ok"] is True, "precondition: this probe must succeed"
    assert PROBE_FAILURE_MESSAGE not in _log(rendered), (
        "the failure message appears in the log of a SUCCESSFUL probe, so the "
        "degradation test's assertion cannot distinguish success from failure"
    )


def test_a_broken_probe_cannot_cost_us_the_verdict(tmp_path: Path) -> None:
    """Pins the hoist, which is the actual fix.

    `|| true` does NOT catch a `set -u` violation — the shell exits outright —
    so while the probe lived inside the verdict block, any unbound variable in
    it destroyed the harness's only oracle. Injecting that exact shape fails
    if the probe is ever moved back inside.
    """
    marker = tmp_path / "webview.enabled"
    marker.touch()
    rendered = _render(tmp_path, marker=marker)
    text = rendered.read_text(encoding="utf-8")
    injected = text.replace(
        "webview_requested=true",
        'webview_requested=true; : "$DEFINITELY_UNBOUND_VAR"',
        1,
    )
    assert injected != text, "probe shape changed; this injection no longer applies"
    rendered.write_text(injected, encoding="utf-8")

    _run(rendered)

    # The property, not the mechanism. With `set +u` scoped to the probe the
    # injection is a silent no-op by design — that IS the containment. Remove
    # the `set +u` (or otherwise let a probe fault escape) and the script dies
    # before the printf, so the verdict vanishes and this fails.
    verdict_path = tmp_path / "verdict.json"
    assert verdict_path.exists(), (
        "a fault in the optional probe destroyed the verdict — the harness's "
        "only oracle. The probe must not be able to abort the script."
    )
    # Degradation is covered separately, by forcing a real import failure —
    # under `set +u` this injection is inert by design, which is the whole
    # point of the containment.
    json.loads(verdict_path.read_text(encoding="utf-8"))


def test_unimportable_package_degrades_instead_of_claiming_success(
    tmp_path: Path,
) -> None:
    """A probe that genuinely fails must say so, not pass silently.

    This is the shape of the original bug: the import failed, nothing recorded
    it, and run.sh reported PASS. Now webview_ok goes false and run.sh fails
    the run under --webview.
    """
    # Only meaningful where PYTHONPATH actually decides importability. If the
    # package is pip-installed for this interpreter it is importable no matter
    # what we set, and the probe correctly reports true. That is exactly the
    # difference between this box and the hwsim box's root user, where the
    # package is NOT installed and the original failure occurred.
    # /usr/bin/python3, NOT sys.executable: that is the interpreter the
    # template hardcodes, and under pytest the two are routinely different
    # (a venv or hostedtoolcache python vs. the system one), so probing
    # sys.executable can skip a case the runner would have failed.
    probe = subprocess.run(
        ["/usr/bin/python3", "-c", "import gatepath.portal_webview_runner"],
        capture_output=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""},
    )
    if probe.returncode == 0:
        pytest.skip(
            "gatepath is importable without PYTHONPATH here (pip-installed), "
            "so a bogus PYTHONPATH cannot force the failure this pins"
        )

    marker = tmp_path / "webview.enabled"
    marker.touch()
    empty = tmp_path / "no-package-here"
    empty.mkdir()
    rendered = _render(tmp_path, marker=marker, pythonpath=str(empty))
    _run(rendered)

    verdict = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["webview_requested"] is True
    assert verdict["webview_ok"] is False, (
        "import failed but the runner still claimed the WebView was ready"
    )
    assert PROBE_FAILURE_MESSAGE in _log(rendered), (
        "the failure was not recorded anywhere an operator would look — the "
        "runner log is the only surviving diagnostic for a broken --webview"
    )
