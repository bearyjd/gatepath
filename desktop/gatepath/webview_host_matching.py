"""Same-origin host matching for the portal WebView.

Decides whether a navigation is **same-origin** or **off-domain (observed and
counted in the audit log, but allowed to load)**. Pure and stdlib-only, so the
rule is testable without a GTK environment.

Counterpart to Android's `ui/WebViewHostMatching.kt`. The two apps share no
code by design, so this is a deliberate re-implementation of the same rule,
and `test_webview_host_matching.py` mirrors `WebViewHostMatchingTest.kt` case
for case.

Match rule:
  - exact host match (case-insensitive, trailing-dot tolerant), OR
  - `request_host` is a subdomain of `portal_host`

Defensive cases:
  - blank `portal_host` -> always False. When no portal host could be parsed,
    count every navigation as off-domain (audit-log noise) rather than treat
    every host as same-origin (audit-log blindness).
  - blank `request_host` -> False.

Pinned bug (would-have-been): a naive `request_host.endswith("." + portal_host)`
with an empty `portal_host` becomes `endswith(".")`, which matches any FQDN
written with a trailing dot. The blank-portal guard prevents this — the same
trap the Kotlin side documents.
"""

from __future__ import annotations


def _normalize_host(host: str) -> str:
    """Lowercase, strip whitespace, drop a trailing root dot."""
    return host.strip().rstrip(".").lower()


def is_same_origin_host(request_host: str, portal_host: str) -> bool:
    """True when `request_host` is the portal host or a subdomain of it."""
    portal = _normalize_host(portal_host)
    request = _normalize_host(request_host)
    if not portal or not request:
        return False
    if request == portal:
        return True
    return request.endswith(f".{portal}")
