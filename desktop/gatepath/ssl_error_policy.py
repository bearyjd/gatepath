"""Policy for certificate errors on the portal page.

Pure and stdlib-only. Counterpart to Android's `ui/SslErrorPolicy.kt` — the two
apps share no code, so this is a deliberate re-implementation of the same rule.

A captive gateway's own login page routinely fails certificate validation:
self-signed, expired, or a CN that is the gateway's RFC1918 IP. Refusing those
makes the portal unusable, so they are proceeded past.

But the portal WebView is **not confined to the portal host** — off-domain
navigation is deliberately allowed for captive-vendor compatibility (see
`webview_host_matching`), so the gateway can steer the page anywhere. Bypassing
unconditionally would therefore disable certificate validation for arbitrary
hosts on the one network where an attacker is on-path by definition: a hostile
gateway could redirect to a real identity provider and MITM it silently.

So the bypass is scoped: **the portal host and its subdomains only; every other
host keeps normal TLS enforcement.** Fails closed — an unparseable host on
either side means "enforce", never "bypass".
"""

from __future__ import annotations

from gatepath.webview_host_matching import is_same_origin_host


def should_proceed(error_host: str, portal_host: str) -> bool:
    """True to trust the certificate for this host, False to refuse the load.

    Args:
        error_host: host of the URL that raised the certificate error.
        portal_host: host parsed out of the captive-portal URL.
    """
    return is_same_origin_host(error_host, portal_host)
