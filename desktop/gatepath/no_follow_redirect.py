"""Shared no-follow redirect handler for urllib-based HTTP callers.

Both the connectivity probe (`portal_probe.py`) and the diagnostic fetcher
(`http_fetcher.py`) need to see a 3xx redirect's `Location` *without*
following it: for a captive-portal check, the redirect target IS the portal
sign-in page, not just a hop en route to the real resource. Following it
would fetch the portal page itself instead of reporting "this URL redirects
to a portal", losing the signal both callers depend on.

`urllib.request.HTTPRedirectHandler.redirect_request` returning `None`
aborts the follow while still handing us the target URL, which is what this
class relies on.
"""

from __future__ import annotations

import urllib.request
from typing import Optional


class NoFollowRedirectHandler(urllib.request.HTTPRedirectHandler):
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
