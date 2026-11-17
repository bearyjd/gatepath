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


WORKFLOWS = sorted((RELEASE.parent).glob("*.yml"))
# Every way a workflow can name a container image: `image:` (container mapping
# and services), the string form `container: <ref>`, and `uses: docker://<ref>`.
# An empty `container:` (mapping form) is skipped; its `image:` line is matched.
IMAGE_REF_RES = (
    re.compile(r"^\s*image:\s*(\S+)", re.M),
    re.compile(r"^\s*container:[ \t]*([^\s#]\S*)", re.M),
    re.compile(r"^\s*(?:-\s+)?uses:\s*docker://(\S+)", re.M),
)
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


def _image_refs(text):
    return [ref for rx in IMAGE_REF_RES for ref in rx.findall(text)]


def test_all_workflows_pin_actions_to_commit_sha():
    unpinned = [
        f"{wf.name}: {ref}"
        for wf in WORKFLOWS
        for ref in USES_RE.findall(wf.read_text())
        if not ref.startswith(("./", "docker://")) and not SHA_RE.match(ref)
    ]
    assert not unpinned, f"actions not pinned to a 40-char commit SHA: {unpinned}"


def test_image_ref_forms_are_all_detected():
    sample = (
        "    container: fedora:latest\n"
        "    container:\n      image: alpine:3\n"
        "    services:\n      db:\n        image: postgres:16\n"
        "      - uses: docker://busybox:1\n"
    )
    assert sorted(_image_refs(sample)) == [
        "alpine:3", "busybox:1", "fedora:latest", "postgres:16"
    ]


def test_workflow_container_images_pinned_by_digest():
    unpinned = [
        f"{wf.name}: {img}"
        for wf in WORKFLOWS
        for img in _image_refs(wf.read_text())
        if not DIGEST_RE.search(img)
    ]
    assert not unpinned, f"container images not pinned by @sha256 digest: {unpinned}"
