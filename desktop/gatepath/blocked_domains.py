"""Blocked tracker/analytics domain list — pure stdlib.

`is_blocked(host)` returns True when the host exactly matches an entry
or is a subdomain of one (e.g. "sub.google-analytics.com" is blocked
because "google-analytics.com" is in the set).

The list mirrors the Android companion app's blocked-domain set.
"""

from __future__ import annotations

BLOCKED_DOMAINS: frozenset[str] = frozenset(
    {
        # Google analytics / ads
        "google-analytics.com",
        "googletagmanager.com",
        "googletagservices.com",
        "googlesyndication.com",
        "doubleclick.net",
        "adservice.google.com",
        # Facebook
        "connect.facebook.net",
        "graph.facebook.com",
        # Common trackers
        "hotjar.com",
        "segment.io",
        "segment.com",
        "mixpanel.com",
        "amplitude.com",
        "fullstory.com",
        "intercom.io",
        "intercomcdn.com",
        "clarity.ms",
        # Ad networks
        "ads.twitter.com",
        "static.ads-twitter.com",
        "analytics.twitter.com",
        "moatads.com",
        "scorecardresearch.com",
        "quantserve.com",
        "rubiconproject.com",
        "pubmatic.com",
        "openx.net",
        # Test fixture from mockportal
        "evil-tracker.example.com",
    }
)


def is_blocked(host: str) -> bool:
    """Return True if *host* (lowercased) matches or is a subdomain of a blocked entry."""
    host = host.lower().rstrip(".")
    if host in BLOCKED_DOMAINS:
        return True
    for entry in BLOCKED_DOMAINS:
        if host.endswith("." + entry):
            return True
    return False
