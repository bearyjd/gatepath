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


def _render(tmp_path: Path, *, marker: Path) -> Path:
    """Substitute the @TOKEN@ placeholders exactly as run.sh's sed does."""
    text = TEMPLATE.read_text(encoding="utf-8")
    subs = {
        "@IFACE@": "lo",
        "@CLIENT_CIDR@": "127.0.0.2/8",
        "@GATEWAY@": "127.0.0.1",
        "@SENTINEL_URL@": f"{DEAD_URL}/health",
        "@VERDICT@": str(tmp_path / "verdict.json"),
        "@WEBVIEW_MARKER@": str(marker),
        "@GATEPATH_PYTHONPATH@": str(REPO_ROOT / "desktop"),
    }
    for token, value in subs.items():
        text = text.replace(token, value)
    # run.sh refuses to install a runner with a leftover placeholder; hold the
    # test to the same bar so a newly-added token can't slip through unnoticed.
    assert "@" not in "".join(
        line for line in text.splitlines() if line.strip().startswith(("VERDICT=", "WEBVIEW_", "GATEPATH_"))
    ), "unsubstituted placeholder in the rendered runner"
    assert EXEC_LINE in text, (
        "the runner no longer execs the real WebView — these tests stub that "
        "line, so its removal would otherwise go unnoticed"
    )
    text = text.replace(EXEC_LINE, 'echo "STUBBED EXEC" >&2; exit 0  #')

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
    result = _run(rendered)

    verdict_path = tmp_path / "verdict.json"
    assert verdict_path.exists(), (
        "no verdict written with the webview marker set — the optional probe "
        f"aborted the runner again. stderr tail:\n{result.stderr[-2000:]}"
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
        result = _run(rendered)
        assert "unbound variable" not in result.stderr, (
            f"unbound variable with marker_present={marker_present}:\n"
            f"{result.stderr[-1000:]}"
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
