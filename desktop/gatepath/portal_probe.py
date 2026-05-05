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
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

CONNECTIVITY_CHECK_URL = "http://connectivity-check.ubuntu.com/"


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    """Immutable result of a single connectivity probe."""

    status: str  # "validated" | "portal" | "error"
    portal_url: Optional[str] = None
    message: Optional[str] = None


class _NoFollowRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Capture redirect Location without following it."""

    def __init__(self) -> None:
        self.redirect_location: Optional[str] = None

    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        # Store the location and return None to abort the follow.
        self.redirect_location = newurl
        return None  # type: ignore[return-value]


def probe(
    url: str = CONNECTIVITY_CHECK_URL,
    timeout: int = 5,
) -> ProbeResult:
    """Probe *url* and classify the network state.

    Returns:
        ProbeResult with status "validated", "portal", or "error".
    """
    redirect_handler = _NoFollowRedirectHandler()
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
                logger.debug("Portal redirect detected: %s -> %s", url, location)
                return ProbeResult(status="portal", portal_url=location)
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
        logger.debug(
            "Portal redirect (no-raise) detected: %s -> %s",
            url,
            redirect_handler.redirect_location,
        )
        return ProbeResult(status="portal", portal_url=redirect_handler.redirect_location)

    if status_code == 204:
        logger.debug("Probe validated (204) for %s", url)
        return ProbeResult(status="validated")

    if status_code == 200:
        logger.debug("Probe got 200 (may be portal intercept) for %s", url)
        return ProbeResult(status="portal", portal_url=url)

    logger.warning("Unexpected probe status %s for %s", status_code, url)
    return ProbeResult(status="error", message=f"Unexpected status {status_code}")
