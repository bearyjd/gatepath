package com.ventouxlabs.gatepath.network

import java.net.URI

/**
 * Extracts a portal location from a 200-response that intercepted the
 * connectivity check, by reading a `Refresh` header or an HTML meta-refresh.
 *
 * Background: many captive gateways (Cisco/Meraki/Cloudflare-style) do not
 * answer the connectivity-check URL with a 3xx. They return 200 and either
 * serve the login page in place or bounce the client onward via a `Refresh`
 * header / `<meta http-equiv="refresh">`. [PortalProbe] treats such a 200 as
 * [ProbeResult.Portal]; this object decides *where* that portal lives.
 *
 * Security posture — every input here is attacker-controlled. The bytes arrive
 * from an unauthenticated gateway before the user has signed in to anything,
 * and whatever this returns is handed to a WebView. Therefore:
 *
 *   - Only `http` and `https` targets are accepted. `javascript:`, `data:`,
 *     `file:`, `intent:` and friends are rejected outright, so a hostile
 *     gateway cannot use the hint to run script in the portal WebView or point
 *     it at local storage.
 *   - Relative targets resolve against the probe URL, never against something
 *     the body controls.
 *   - Scanning is capped at [MAX_HTML_SCAN_CHARS] so a huge body cannot turn
 *     into unbounded regex work.
 *   - Anything unparseable yields `null`, and the caller falls back to the
 *     probe URL itself. Failing to find a hint is safe; guessing is not.
 */
object PortalRedirectHint {

    /**
     * Upper bound on how much of a response body is scanned for a meta-refresh.
     * Real portal pages put the tag in `<head>`, within the first few hundred
     * bytes; anything past this cap is treated as absent.
     */
    const val MAX_HTML_SCAN_CHARS = 64 * 1024

    private val ALLOWED_SCHEMES = setOf("http", "https")

    /** `url=<target>` inside a refresh directive, quoted or bare. */
    private val REFRESH_URL = Regex(
        """url\s*=\s*(?:"([^"]*)"|'([^']*)'|([^;,\s]*))""",
        RegexOption.IGNORE_CASE,
    )

    private val META_TAG = Regex("""<meta\s[^>]*>""", RegexOption.IGNORE_CASE)

    /**
     * `top.location.href = "..."` and friends — the third redirect form seen in
     * the field, used by gateways that emit neither a Refresh header nor a
     * meta-refresh. Covers the `top.` / `window.` / `self.` / `parent.`
     * prefixes, an optional `.href`, and `location.replace("...")`.
     *
     * This is a deliberately shallow string match, not JavaScript evaluation:
     * it recovers the portal URL for the common one-line bounce and gives up on
     * anything more elaborate, which is the correct trade for untrusted input.
     */
    private val JS_LOCATION = Regex(
        """(?:top|window|self|parent)?\.?location(?:\.href)?\s*(?:=|\.replace\s*\()\s*["']([^"']+)["']""",
        RegexOption.IGNORE_CASE,
    )

    /**
     * Best-effort portal location for a 200 response, or `null` when no usable
     * hint is present.
     *
     * [refreshHeader] takes precedence over [html]: a header is set by the
     * responding server itself, while body markup may have been injected into
     * a page the gateway is proxying. Within the body, a meta-refresh wins over
     * a scripted `location` assignment — the former is declarative and
     * unambiguous, the latter is a string match on code we do not execute.
     */
    fun resolve(refreshHeader: String?, html: String?, baseUrl: String): String? =
        fromRefreshDirective(refreshHeader, baseUrl)
            ?: fromHtml(html, baseUrl)
            ?: fromScriptedLocation(html, baseUrl)

    /** Parse a `Refresh: 0; url=...` directive value. */
    private fun fromRefreshDirective(directive: String?, baseUrl: String): String? {
        if (directive.isNullOrBlank()) return null
        val match = REFRESH_URL.find(directive) ?: return null
        return sanitize(firstNonNullGroup(match), baseUrl)
    }

    /** Find the first `<meta http-equiv="refresh">` and parse its `content`. */
    private fun fromHtml(html: String?, baseUrl: String): String? {
        if (html.isNullOrEmpty()) return null
        val scannable = html.take(MAX_HTML_SCAN_CHARS)
        for (tag in META_TAG.findAll(scannable)) {
            val httpEquiv = attribute(tag.value, "http-equiv") ?: continue
            if (!httpEquiv.trim().equals("refresh", ignoreCase = true)) continue
            val content = attribute(tag.value, "content") ?: continue
            fromRefreshDirective(content, baseUrl)?.let { return it }
        }
        return null
    }

    /**
     * Find a `location` assignment in inline script. Observed in the field on a
     * gateway that answers the connectivity check with a 200 whose entire body
     * is `<script>top.location.href="http://<gateway>/portal/entry?...">`.
     *
     * Without this, such a portal still loads — the WebView runs the script —
     * but Gatepath would record the probe URL as the portal host, so the whole
     * real session lands in the audit log as off-domain.
     */
    private fun fromScriptedLocation(html: String?, baseUrl: String): String? {
        if (html.isNullOrEmpty()) return null
        val match = JS_LOCATION.find(html.take(MAX_HTML_SCAN_CHARS)) ?: return null
        return sanitize(match.groupValues.getOrNull(1), baseUrl)
    }

    /** Read a single attribute value out of one tag, quoted or bare. */
    private fun attribute(tag: String, name: String): String? {
        val pattern = Regex(
            """(?<![\w-])${Regex.escape(name)}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*))""",
            RegexOption.IGNORE_CASE,
        )
        val match = pattern.find(tag) ?: return null
        return firstNonNullGroup(match)
    }

    private fun firstNonNullGroup(match: MatchResult): String? =
        match.groupValues.drop(1).firstOrNull { it.isNotEmpty() }

    /**
     * Resolve [target] against [baseUrl] and accept it only if it is an
     * absolute http(s) URL with a host. Returns `null` for every other case,
     * including syntactically invalid input.
     */
    private fun sanitize(target: String?, baseUrl: String): String? {
        val trimmed = target?.trim().orEmpty()
        if (trimmed.isEmpty()) return null
        return runCatching {
            val resolved = URI(baseUrl).resolve(trimmed)
            val scheme = resolved.scheme?.lowercase()
            when {
                scheme !in ALLOWED_SCHEMES -> null
                resolved.host.isNullOrEmpty() -> null
                else -> resolved.toString()
            }
        }.getOrNull()
    }
}
