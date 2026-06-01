"""UIAutomator helpers using `uiautomator dump` + `input tap`. Stdlib only.

Pattern: dump UI to XML, find a node by visible text, compute the midpoint
of its bounds, tap. Sufficient for the chooser-tap and "Sign in to Wi-Fi"
notification-tap that this harness needs.

Not suitable for in-WebView form interaction at API 34 — WebView contents
are often opaque to UIAutomator unless `setWebContentsDebuggingEnabled(true)`
and the page exposes accessibility nodes. Scenario falls back to host-side
POST in `--mode=host-post` for that reason.
"""

from __future__ import annotations

import re
import time
from typing import Optional
from xml.etree import ElementTree as ET

from adb_helper import shell

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def dump_ui_xml(serial: str) -> str:
    """Raw UI-hierarchy XML as a string.

    Uses --compressed to keep the XML small. Falls back to plain dump if
    --compressed is rejected (some API levels). Strips any preamble line like
    "UI hierchary dumped to: ..." some builds emit before the XML.
    """
    try:
        shell(serial, "uiautomator dump --compressed /sdcard/ui.xml", timeout=15)
    except RuntimeError:
        shell(serial, "uiautomator dump /sdcard/ui.xml", timeout=15)
    xml = shell(serial, "cat /sdcard/ui.xml", timeout=10)
    return xml[xml.index("<") :] if "<" in xml else xml


def dump_ui(serial: str) -> ET.Element:
    """Dump the current UI hierarchy and return the parsed root."""
    return ET.fromstring(dump_ui_xml(serial))


def parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    """child -> parent map (ElementTree nodes don't track parents)."""
    return {child: parent for parent in root.iter("node") for child in parent}


def clickable_ancestor(node: ET.Element, parents: dict) -> Optional[ET.Element]:
    """Walk up from `node` to the nearest `clickable="true"` ancestor.

    A notification's title TextView is usually not itself clickable — tapping
    it does not fire the notification's contentIntent. The enclosing row is
    the clickable target."""
    cur: Optional[ET.Element] = node
    while cur is not None:
        if cur.attrib.get("clickable") == "true":
            return cur
        cur = parents.get(cur)
    return None


def find_by_text(root: ET.Element, text: str) -> Optional[ET.Element]:
    """Exact-match search on the `text` attribute."""
    for node in root.iter("node"):
        if node.attrib.get("text") == text:
            return node
    return None


def find_by_text_contains(root: ET.Element, fragment: str) -> Optional[ET.Element]:
    """Case-insensitive substring search on the `text` attribute."""
    needle = fragment.lower()
    for node in root.iter("node"):
        if needle in node.attrib.get("text", "").lower():
            return node
    return None


def find_by_resource_id(root: ET.Element, rid: str) -> Optional[ET.Element]:
    """Exact match on `resource-id`."""
    for node in root.iter("node"):
        if node.attrib.get("resource-id") == rid:
            return node
    return None


def midpoint(node: ET.Element) -> tuple[int, int]:
    """Midpoint of the node's `bounds` rect. Raises if unparseable."""
    bounds = node.attrib.get("bounds", "")
    m = BOUNDS_RE.match(bounds)
    if not m:
        raise RuntimeError(f"unparseable bounds: {bounds!r}")
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def tap(serial: str, x: int, y: int) -> None:
    """`input tap X Y`."""
    shell(serial, f"input tap {x} {y}")


def wait_for_text(
    serial: str, text: str, timeout: float = 30.0, poll: float = 0.5
) -> ET.Element:
    """Poll dump_ui until a node with exactly `text` appears, or raise."""
    deadline = time.monotonic() + timeout
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            root = dump_ui(serial)
        except Exception as e:
            last_err = e
            time.sleep(poll)
            continue
        node = find_by_text(root, text)
        if node is not None:
            return node
        time.sleep(poll)
    raise RuntimeError(
        f"timed out waiting for text {text!r} (last dump error: {last_err})"
    )


def wait_for_text_contains(
    serial: str, fragment: str, timeout: float = 30.0, poll: float = 0.5
) -> ET.Element:
    """Like wait_for_text but uses substring match."""
    deadline = time.monotonic() + timeout
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            root = dump_ui(serial)
        except Exception as e:
            last_err = e
            time.sleep(poll)
            continue
        node = find_by_text_contains(root, fragment)
        if node is not None:
            return node
        time.sleep(poll)
    raise RuntimeError(
        f"timed out waiting for text containing {fragment!r} "
        f"(last dump error: {last_err})"
    )


def tap_text(serial: str, text: str, timeout: float = 30.0) -> None:
    """Convenience: wait for a node with `text`, tap its midpoint."""
    node = wait_for_text(serial, text, timeout=timeout)
    x, y = midpoint(node)
    tap(serial, x, y)
