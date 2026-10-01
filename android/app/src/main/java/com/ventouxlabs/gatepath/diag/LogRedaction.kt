package com.ventouxlabs.gatepath.diag

import java.net.URI

/**
 * Strips device identifiers from text bound for the exported sign-in log.
 *
 * Captive-portal URLs carry the device's MAC address, its IP and session
 * tokens in their paths and query strings (a real hotel portal put
 * `MA=<mac>` and `SIP=<ip>` in every redirect), and gateways may send them
 * as relative `Location` headers. Two layers:
 *
 * - [origin] for values known to be URLs: parsed structurally, reduced to
 *   scheme + host (+ port). Relative or unparseable URLs become a
 *   placeholder, never pass through. Call sites use this.
 * - [redact] for every recorded line, as a backstop: URLs it recognises (any
 *   scheme) are reduced to their origin, and everywhere else query and
 *   fragment runs are dropped and MAC addresses (every common shape) and IP
 *   addresses are masked.
 *
 * Hostnames are kept by design, and can themselves identify a venue or a
 * session. Pure Kotlin so the redaction is JVM-tested.
 */
object LogRedaction {

    /** An absolute URL with an authority, any scheme (http, https, intent, ws…). */
    private val URL = Regex("""\b([A-Za-z][A-Za-z0-9+.-]*)://([^\s/?#"'<>()]+)[^\s"'<>()]*""")

    /** Any query or fragment run outside a recognised URL (a relative Location, say). */
    private val QUERY_OR_FRAGMENT = Regex("""[?#][^\s"'<>()]*""")

    private val MAC_SEPARATED =
        Regex("""(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])""")
    private val MAC_PERCENT_ENCODED = Regex("""(?i)(?<![0-9a-f])[0-9a-f]{2}(?:%3A[0-9a-f]{2}){5}(?![0-9a-f])""")
    private val MAC_DOTTED = Regex("""(?i)(?<![0-9a-f.])[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}(?![0-9a-f.])""")

    /** A bare 12-hex MAC, only right after a mac-like key, so ordinary hex ids are left alone. */
    private val MAC_KEYED = Regex("""(?i)\b((?:user_)?mac(?:_address)?|ma)(\s*[=:]\s*)[0-9a-f]{12}(?![0-9a-f])""")

    /**
     * Scheme + host (+ port) of [url]. `(none)` for null, `(relative URL)`
     * for a scheme-less one, `scheme:(no host)` for a URL without a host, and
     * `(unparseable URL)` when it can't be parsed. Never returns the input.
     */
    fun origin(url: String?): String {
        if (url == null) return "(none)"
        val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return "(unparseable URL)"
        val scheme = uri.scheme?.lowercase() ?: return "(relative URL)"
        val host = uri.host?.takeIf { it.isNotEmpty() } ?: return "$scheme:(no host)"
        return if (uri.port == -1) "$scheme://$host" else "$scheme://$host:${uri.port}"
    }

    /** [text] with recognised URLs reduced to their origin and identifiers masked everywhere else. */
    fun redact(text: String): String {
        val out = StringBuilder()
        var last = 0
        for (m in URL.findAll(text)) {
            out.append(maskPlainText(text.substring(last, m.range.first)))
            out.append(m.groupValues[1].lowercase()).append("://").append(m.groupValues[2].substringAfterLast('@'))
            last = m.range.last + 1
        }
        out.append(maskPlainText(text.substring(last)))
        return out.toString()
    }

    private fun maskPlainText(text: String): String {
        var r = QUERY_OR_FRAGMENT.replace(text, "")
        r = MAC_KEYED.replace(r) { "${it.groupValues[1]}${it.groupValues[2]}[mac]" }
        r = MAC_PERCENT_ENCODED.replace(r, "[mac]")
        r = MAC_SEPARATED.replace(r, "[mac]")
        r = MAC_DOTTED.replace(r, "[mac]")
        r = DiagnosticsBundle.IPV6.replace(r, "[ip]")
        return DiagnosticsBundle.IPV4.replace(r, "[ip]")
    }
}
