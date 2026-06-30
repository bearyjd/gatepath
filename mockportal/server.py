"""Mock captive portal HTTP server for Gatepath integration tests.

Behavior:
  GET /generate_204 -> 302 to /portal while captive, then 204 No Content once
                        authenticated. "Captive" = not yet logged in AND within
                        the first PORTAL_COMPLETE_AFTER probes; a successful
                        /login flips the session to authenticated so every
                        subsequent probe returns 204 (models sign-in).
  GET /portal       -> minimal HTML login page with intentional off-domain
                        tracker script and external link (used to verify blocking).
  POST /login       -> marks the session authenticated, 302 to /generate_204.
  POST /reset       -> resets the counter, auth flag, and request log.
  GET /log          -> JSON array of all requests received (test assertions).

Configurable via env: PORTAL_HOST, PORTAL_PORT, PORTAL_COMPLETE_AFTER (default 3).
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# PORTAL_HOST defaults to loopback so the test endpoint isn't exposed on the
# LAN: /log returns request headers verbatim, and exposing that to non-loopback
# peers would leak Authorization tokens passed to the mock during integration
# tests. It is overridable via the PORTAL_HOST env var for harnesses that must
# bind a routable test IP (e.g. the mac80211_hwsim AP gateway address, which is
# reachable only over an isolated virtual-radio link). NEVER set PORTAL_HOST to
# a LAN-routable address outside a throwaway test network: /log would then
# expose verbatim request headers to anything that can reach that interface.
PORTAL_HOST = os.environ.get("PORTAL_HOST", "127.0.0.1")
PORTAL_PORT = int(os.environ.get("PORTAL_PORT", "18080"))
PORTAL_COMPLETE_AFTER = int(os.environ.get("PORTAL_COMPLETE_AFTER", "3"))
# Optional BASE url for the android no-leak sentinel (PR #55). When set, /portal
# injects a <head> carrying BOTH a favicon <link> and a blocking <script>, each
# pointing at this base — the favicon is the one sub-resource a captive-portal
# WebView fetches reliably before the session tears down, and the head script is
# a deterministic second trigger. Default empty: when unset the portal HTML is
# served byte-for-byte unchanged, so the desktop e2e path is unaffected. The
# android harness sets it to a dedicated sentinel host:port (e.g.
# http://10.0.2.2:18081) the captive monitor never touches, making bound-phase
# WebView traffic unambiguous in the VPN sink.
PORTAL_LEAK_SENTINEL = os.environ.get("PORTAL_LEAK_SENTINEL", "")


PORTAL_HTML = """<!doctype html>
<html><body>
<h1>Test Portal</h1>
<form method="POST" action="/login">
  <input name="user" placeholder="username">
  <button type="submit">Connect</button>
</form>
<script src="https://evil-tracker.example.com/track.js"></script>
<a href="https://external-site.example.com">External link</a>
</body></html>
"""


class _State:
    """Shared, thread-safe state for the mock portal."""

    def __init__(self, complete_after: int) -> None:
        self._lock = threading.Lock()
        self._complete_after = complete_after
        self.probe_count = 0
        self.authenticated = False
        self.requests: list[dict[str, Any]] = []

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(entry)

    def consume_probe(self) -> bool:
        """Return True if this probe should still redirect to /portal.

        Once /login has been POSTed the session is authenticated and every
        probe validates (204) — this models a real captive portal: captive
        until you sign in, open afterwards. Before login, the counter governs
        behaviour so callers that never log in (the desktop e2e, unit tests)
        keep the redirect-for-first-N semantics. complete_after is set high in
        the Android harness so the network stays reliably captive until the
        /login signal arrives, rather than auto-validating mid-detection."""
        with self._lock:
            self.probe_count += 1
            if self.authenticated:
                return False
            return self.probe_count <= self._complete_after

    def mark_authenticated(self) -> None:
        """Record a successful /login — subsequent probes return 204."""
        with self._lock:
            self.authenticated = True

    def reset(self) -> None:
        with self._lock:
            self.probe_count = 0
            self.authenticated = False
            self.requests.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.requests)


def _make_handler(
    state: _State, host: str, port: int, leak_sentinel: str
) -> type[BaseHTTPRequestHandler]:
    portal_url = f"http://{host}:{port}/portal"
    probe_url = f"http://{host}:{port}/generate_204"

    class Handler(BaseHTTPRequestHandler):
        # Silence default per-request stderr logging during tests.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _record(self, body: bytes | None = None) -> None:
            state.record(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": (body.decode("utf-8", errors="replace") if body else None),
                }
            )

        def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._record()
            if self.path.startswith("/generate_204"):
                if state.consume_probe():
                    self._send(302, headers={"Location": portal_url})
                else:
                    self._send(204)
                return
            if self.path.startswith("/portal"):
                # Build the response per-request so the module-level PORTAL_HTML
                # constant is never mutated. With leak_sentinel empty the body is
                # byte-identical to PORTAL_HTML (desktop e2e unaffected); when set,
                # inject a <head> carrying a favicon <link> and a blocking <script>,
                # both pointing at the sentinel base. The favicon is the one
                # sub-resource a captive-portal WebView fetches reliably before the
                # session tears down; the head script is a deterministic second
                # trigger (the android no-leak sentinel).
                html = PORTAL_HTML
                if leak_sentinel:
                    base = leak_sentinel.rstrip("/")
                    head = (
                        f"<head>"
                        f'<link rel="icon" href="{base}/favicon.ico">'
                        f'<script src="{base}/leak.js"></script>'
                        f"</head>"
                    )
                    html = html.replace("<body>", f"{head}\n<body>", 1)
                self._send(
                    200,
                    html.encode("utf-8"),
                    {"Content-Type": "text/html; charset=utf-8"},
                )
                return
            if self.path.startswith("/log"):
                payload = json.dumps(state.snapshot()).encode("utf-8")
                self._send(200, payload, {"Content-Type": "application/json"})
                return
            self._send(404, b"not found", {"Content-Type": "text/plain"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            self._record(body)
            if self.path.startswith("/login"):
                state.mark_authenticated()
                self._send(302, headers={"Location": probe_url})
                return
            if self.path.startswith("/reset"):
                state.reset()
                self._send(200, b'{"status":"reset"}', {"Content-Type": "application/json"})
                return
            self._send(404, b"not found", {"Content-Type": "text/plain"})

    return Handler


def build_server(
    host: str = PORTAL_HOST,
    port: int = PORTAL_PORT,
    complete_after: int = PORTAL_COMPLETE_AFTER,
    leak_sentinel: str = PORTAL_LEAK_SENTINEL,
) -> tuple[ThreadingHTTPServer, _State]:
    state = _State(complete_after)
    server = ThreadingHTTPServer((host, port), lambda *a, **kw: None)
    actual_host, actual_port = server.server_address[:2]
    server.RequestHandlerClass = _make_handler(state, actual_host, actual_port, leak_sentinel)
    return server, state


def main() -> None:
    server, _ = build_server()
    host, port = server.server_address[:2]
    print(f"mockportal listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
