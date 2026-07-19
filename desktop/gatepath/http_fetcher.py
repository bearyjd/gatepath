"""Diagnostic HTTP fetcher — pure stdlib (urllib only), no GTK/requests/httpx.

Single-request no-follow GET for the diagnostic battery: redirects are
reported, never followed; the `Date` response header is surfaced (parsed to
epoch seconds) for clock-skew detection; the body is capped at 64 KiB. Lives
outside `diag/` because it does I/O — `diag/` stays pure and only depends on
the `HttpFetchResult` type declared in `gatepath.diag.probe`, never on this
module.

Mirrors Android's `HttpFetcher.kt` (network/HttpFetcher.kt): same no-follow
redirect handling, same Date-header parsing intent, same 64 KiB body cap.

Usage:
    result = fetch(url)
    if result.error is not None:
        ...
"""

from __future__ import annotations

import email.utils
import logging
import urllib.error
import urllib.request
from typing import Optional

from gatepath.diag.probe import HttpFetchResult
from gatepath.no_follow_redirect import NoFollowRedirectHandler

logger = logging.getLogger(__name__)

# Cap mirrors the Android fetcher's MAX_BODY_BYTES: diagnostic pages/DoH
# answers are tiny, so any larger body is truncated rather than fully read.
_MAX_BODY_BYTES = 64 * 1024

_DEFAULT_TIMEOUT_SECONDS = 2.0


def _parse_date_header(value: Optional[str]) -> Optional[float]:
    """Parse an RFC-1123 Date header to epoch seconds; None if absent/malformed."""
    if value is None:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None


def _read_capped_body(response: object) -> Optional[str]:
    """Read at most _MAX_BODY_BYTES from *response* and decode as UTF-8."""
    if response is None:
        return None
    raw = response.read(_MAX_BODY_BYTES)  # type: ignore[attr-defined]
    return raw.decode("utf-8", errors="replace")


def fetch(
    url: str,
    accept: Optional[str] = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> HttpFetchResult:
    """Fetch *url* with a single no-follow GET.

    Never raises: `urllib.error.*`, `OSError`, and `ValueError` are all
    caught and reported via `HttpFetchResult.error`. An `HTTPError` still
    carries a status code, headers, and (capped) body — it is reported as a
    normal result, not as an error.
    """
    redirect_handler = NoFollowRedirectHandler()
    opener = urllib.request.build_opener(redirect_handler)

    headers = {"Accept": accept} if accept is not None else {}
    request = urllib.request.Request(url, headers=headers, method="GET")

    try:
        response = opener.open(request, timeout=timeout)
        try:
            status_code = response.getcode()
            # A 3xx never reaches this branch — it goes through the
            # HTTPError branch below — so redirect_handler.redirect_location
            # is always None here; just read the header directly.
            location = response.headers.get("Location")
            date_epoch_seconds = _parse_date_header(response.headers.get("Date"))
            body = _read_capped_body(response)
        finally:
            response.close()
        return HttpFetchResult(
            status_code=status_code,
            location=location,
            date_epoch_seconds=date_epoch_seconds,
            body=body,
            error=None,
        )
    except urllib.error.HTTPError as exc:
        # HTTPError still carries a status code and headers — report it as
        # a result, not an error. exc itself is the file-like error body.
        try:
            location = redirect_handler.redirect_location or exc.headers.get("Location")
            date_epoch_seconds = _parse_date_header(exc.headers.get("Date"))
            body = _read_capped_body(exc)
        finally:
            exc.close()
        return HttpFetchResult(
            status_code=exc.code,
            location=location,
            date_epoch_seconds=date_epoch_seconds,
            body=body,
            error=None,
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("HTTP fetch failed for %s: %s", url, exc)
        return HttpFetchResult(
            status_code=None,
            location=None,
            date_epoch_seconds=None,
            body=None,
            error=str(exc),
        )
