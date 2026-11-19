"""Captive-portal probe — pure stdlib (urllib only), no GTK/requests/httpx.

Sends a GET to the connectivity-check URL.  A custom redirect handler
prevents urllib from following 302s; instead the redirect location is
captured and returned in the ProbeResult.

Usage:
    result = probe()
    if result.status == "portal":
        # result.portal_url is the captive portal URL
"""

from __future__ import annotations

import dataclasses
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from gatepath.no_follow_redirect import NoFollowRedirectHandler

logger = logging.getLogger(__name__)

# MUST be HTTP, not HTTPS — captive portals work by intercepting cleartext
# HTTP and redirecting to a sign-in page. An HTTPS check would either succeed
# (TLS termination by the portal, broken cert validation) or fail with a cert
# error, neither of which distinguishes "captive portal active" from "internet
# down."
#
# Expected responses for this URL (Ubuntu's connectivity-check endpoint):
#   204 No Content       → connectivity validated, no portal
#   301/302/307/308      → captive portal redirect; Location is the portal URL
#   200 OK               → ALSO treated as portal (Cisco / Cloudflare style
#                          intercepts return 200 with a login page; the
#                          desktop probe accepts this where Android does not,
#                          because NetworkManager-supplied URLs may target
#                          endpoints that legitimately return 200)
#   timeout / DNS error  → ProbeResult(status="error")
CONNECTIVITY_CHECK_URL = "http://connectivity-check.ubuntu.com/"


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    """Immutable result of a single connectivity probe."""

    status: str  # "validated" | "portal" | "error"
    portal_url: Optional[str] = None
    message: Optional[str] = None


def resolve_portal_url(location: str, base_url: str) -> Optional[str]:
    """Resolve a redirect *location* against *base_url*; None if not http(s) with a host.

    The Location header is attacker-controlled on a hostile network and is later
    loaded in a WebView, so only absolute http/https URLs with a host pass.
    """
    candidate = (location or "").strip()
    if not candidate:
        return None
    try:
        own = urllib.parse.urlsplit(candidate)
        if own.scheme and not own.hostname:
            return None  # e.g. "http://" would otherwise inherit the base host
        resolved = urllib.parse.urljoin(base_url, candidate)
        parts = urllib.parse.urlsplit(resolved)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    return resolved


def _portal_result(location: str, base_url: str) -> ProbeResult:
    safe = resolve_portal_url(location, base_url)
    if safe is None:
        logger.warning("Rejected unsafe portal redirect Location from %s", base_url)
        return ProbeResult(status="error", message="Unsafe or invalid portal redirect Location")
    logger.debug("Portal redirect detected: %s -> %s", base_url, safe)
    return ProbeResult(status="portal", portal_url=safe)


def probe(
    url: str = CONNECTIVITY_CHECK_URL,
    timeout: int = 5,
) -> ProbeResult:
    """Probe *url* and classify the network state.

    Returns:
        ProbeResult with status "validated", "portal", or "error".
    """
    redirect_handler = NoFollowRedirectHandler()
    opener = urllib.request.build_opener(redirect_handler)

    try:
        response = opener.open(url, timeout=timeout)
        status_code: int = response.getcode()
        response.close()
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        # HTTPError is raised for 3xx when the redirect handler aborts.
        # urllib raises HTTPError with the redirect code in some versions.
        if status_code in (301, 302, 303, 307, 308):
            location = redirect_handler.redirect_location or exc.headers.get("Location")
            if location:
                return _portal_result(location, url)
        logger.warning("Probe HTTP error %s for %s", status_code, url)
        return ProbeResult(
            status="error",
            message=f"HTTP {status_code}: {exc.reason}",
        )
    except urllib.error.URLError as exc:
        logger.warning("Probe URL error for %s: %s", url, exc.reason)
        return ProbeResult(status="error", message=str(exc.reason))
    except OSError as exc:
        logger.warning("Probe OS error for %s: %s", url, exc)
        return ProbeResult(status="error", message=str(exc))

    # Check if the redirect handler caught a 302 without raising HTTPError.
    if redirect_handler.redirect_location is not None:
        return _portal_result(redirect_handler.redirect_location, url)

    if status_code == 204:
        logger.debug("Probe validated (204) for %s", url)
        return ProbeResult(status="validated")

    if status_code == 200:
        logger.debug("Probe got 200 (may be portal intercept) for %s", url)
        return ProbeResult(status="portal", portal_url=url)

    logger.warning("Unexpected probe status %s for %s", status_code, url)
    return ProbeResult(status="error", message=f"Unexpected status {status_code}")
