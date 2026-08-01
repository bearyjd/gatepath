"""`run.sh` must reject a malformed flag instead of hanging on it.

`sudo bash tests/e2e-hwsim/run.sh --dhcp static` is easy to line-wrap in a
terminal, and when the value is lost the script does not fail — it spins
forever at 100% CPU with no module load, no netns, and no artifacts, giving
every appearance of a harness that is slowly working:

    --dhcp) DHCP_MODE="${2:-}"; shift 2 || true ;;

With `--dhcp` as the last argument, `shift 2` fails because there is only one
positional left. `|| true` swallows that failure, so `$#` never decreases, the
`while [ $# -gt 0 ]` guard stays true, and `$1` is `--dhcp` on every iteration.
The `*) die "--dhcp must be 'static' or 'real'"` guard below the loop is never
reached, because the loop never ends.

Observed live on the hwsim host: ~20 minutes in state R+ with no children and
no progress, mistaken for a long-running test.

These tests run the real script as a normal user. Argument parsing sits well
above the root check (`run.sh` requires root only much later), so a
non-privileged invocation exercises the parser and stops at "must be root" —
which is itself the signal that parsing accepted the flags.

`bash -n` would not catch this: an infinite loop is valid syntax.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_SH = REPO_ROOT / "tests" / "e2e-hwsim" / "run.sh"

# Generous enough that a slow machine never flakes, short enough that a hang is
# still caught quickly. The bug this guards is an unbounded spin, not slowness.
TIMEOUT_SEC = 15


def _run(*args: str) -> subprocess.CompletedProcess:
    """Invoke run.sh as the current (non-root) user.

    Raises subprocess.TimeoutExpired if the parser hangs — which is the failure
    mode under test, so callers assert on its absence rather than catching it.
    """
    return subprocess.run(
        ["bash", str(RUN_SH), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SEC,
        cwd=REPO_ROOT,
    )


def test_run_sh_exists() -> None:
    assert RUN_SH.is_file(), RUN_SH


# ── The bug ───────────────────────────────────────────────────────────────


def test_dhcp_without_a_value_terminates_instead_of_hanging() -> None:
    """The regression: a valueless --dhcp must not spin forever."""
    try:
        proc = _run("--dhcp")
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"run.sh --dhcp did not terminate within {TIMEOUT_SEC}s — the "
            "argument loop is spinning (shift 2 fails, || true swallows it, "
            "$# never decreases)"
        )
    assert proc.returncode != 0, "a valueless --dhcp must be an error"


def test_dhcp_without_a_value_says_what_is_wrong() -> None:
    """Terminating is not enough — it has to name the flag.

    The person hitting this has just had a value eaten by their terminal; the
    message is what tells them that rather than sending them to debug the radio.
    """
    proc = _run("--dhcp")
    combined = proc.stdout + proc.stderr
    assert "--dhcp" in combined, combined


# ── Behaviour that must not regress ───────────────────────────────────────


@pytest.mark.parametrize("args", [("--dhcp=static",), ("--dhcp", "static")])
def test_valid_dhcp_forms_pass_parsing(args: tuple[str, ...]) -> None:
    """Both spellings are accepted and reach the root check.

    Reaching "must be root" proves the parser accepted the flags and fell
    through to the next stage — the furthest a non-privileged run can get.
    """
    proc = _run(*args)
    combined = proc.stdout + proc.stderr
    assert "must be root" in combined, combined
    assert "--dhcp must be" not in combined, combined


def test_invalid_dhcp_value_is_rejected_by_name() -> None:
    proc = _run("--dhcp", "bogus")
    combined = proc.stdout + proc.stderr
    assert "--dhcp must be" in combined, combined


def test_empty_dhcp_value_is_treated_as_missing() -> None:
    """`--dhcp ""` is a lost value, not an invalid one — say so."""
    proc = _run("--dhcp", "")
    combined = proc.stdout + proc.stderr
    assert "needs a value" in combined, combined


def test_unknown_flag_is_rejected() -> None:
    proc = _run("--not-a-flag")
    combined = proc.stdout + proc.stderr
    assert "unknown flag" in combined, combined


def test_static_dhcp_shim_installs_a_resolver() -> None:
    """The static shim must set DNS, not just an address and a route.

    A real DHCP client installs the lease's DNS option. Without it the netns
    has connectivity by bare IP and none by hostname — which is exactly how
    #142 stayed hidden: the harness only ever navigates to
    `http://192.168.77.1/portal`, so nothing ever needed to resolve a name.
    """
    body = RUN_SH.read_text()
    shim = body.split('cat > "$WORKDIR/bin/udhcpc"', 1)[1].split("\nEOF\n", 1)[0]
    assert "/etc/resolv.conf" in shim, "static shim installs no resolver"
    assert "nameserver" in shim, shim[-400:]


def test_dhcp_does_not_swallow_a_following_flag() -> None:
    """`--dhcp --webview` is a missing value, not a value of "--webview".

    Consuming the next flag leaves WEBVIEW=0 and DHCP_MODE="--webview", and
    the resulting "must be 'static' or 'real', got '--webview'" points at the
    one flag the user typed correctly. The error has to blame --dhcp.
    """
    proc = _run("--dhcp", "--webview")
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined
    assert "needs a value" in combined, combined
    assert "got '--webview'" not in combined, combined
