"""Supply-chain guards for .github/workflows/release.yml.

The release jobs hold `id-token: write` / `contents: write` and the Android
keystore secrets, so a mutable third-party action tag (a moved `@v3`) would run
attacker-controlled code with signing power. Every `uses:` must therefore be
pinned to a full commit SHA, and secrets must never reach a step that runs a
third-party action.
"""
import re
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"
SHA_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.M)


def _steps(text):
    """Split the workflow into step blocks (each starts at a `- ` list item)."""
    return re.split(r"(?m)^(?=      - )", text)


def test_all_actions_pinned_to_commit_sha():
    unpinned = [
        ref
        for ref in USES_RE.findall(RELEASE.read_text())
        if not ref.startswith("./") and not SHA_RE.match(ref)
    ]
    assert not unpinned, f"actions not pinned to a 40-char commit SHA: {unpinned}"


def test_no_secrets_in_steps_running_third_party_actions():
    leaks = []
    for step in _steps(RELEASE.read_text()):
        m = USES_RE.search(step)
        if m and not m.group(1).startswith("./") and "secrets." in step:
            leaks.append(m.group(1))
    assert not leaks, f"secrets exposed to third-party action steps: {leaks}"
