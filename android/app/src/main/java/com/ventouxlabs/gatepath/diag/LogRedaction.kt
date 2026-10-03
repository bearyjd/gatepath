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
 * - [redact] for every recorded line, as a backstop. In order: Unicode
 *   whitespace (line breaks, tabs, every space) becomes a plain space and
 *   every other control U+FFFD;
 *   delimiter escapes are decoded (an escape ends in a hex digit, which
 *   would otherwise hide the identifier after it from every matcher); query
 *   and fragment runs are dropped, so a URL nested in one goes with it; MAC
 *   addresses (the common shapes) are masked; recognised URLs (any scheme)
 *   are reduced through [origin]; and IP addresses outside them are masked.
 *   Every pattern runs in linear time: a gateway controls the probe's error
 *   text, which is recorded on the main thread.
 *
 * Escapes of letters, digits and `-._~` stay encoded (see [KEPT_ENCODED]),
 * so a MAC or IP whose own characters are escaped is not recognised. A
 * conforming encoder never escapes those characters.
 *
 * Hostnames are kept by design, and can themselves identify a venue or a
 * session. Pure Kotlin so the redaction is JVM-tested.
 */
object LogRedaction {

    /**
     * An absolute URL with an authority: any scheme (http, https, intent,
     * ws…), or one [maskMacs] already replaced. The scheme is found only at
     * the start of a run of scheme characters, with digits and `+.-` in front
     * of its first letter swallowed. An ASCII lookbehind does this rather than
     * a word boundary: there is one before every letter of `a.a.a…`, and
     * rescanning the run from each made matching quadratic on a gateway's
     * text. ICU's word boundary also treats any letter as a word character.
     */
    private val URL =
        Regex("""(?:(?<![A-Za-z0-9+.-])[0-9+.-]*([A-Za-z][A-Za-z0-9+.-]*)|(\[mac\]))://([^\s/?#"'<>()]+)[^\s"'<>()]*""")

    /** Any query or fragment run (a relative Location, or one holding a nested URL). */
    private val QUERY_OR_FRAGMENT = Regex("""[?#][^\s"'<>()]*""")

    /**
     * Unicode White_Space: line breaks, tabs, line and paragraph separators
     * and every space. Replaced by a plain space so a recorded line stays one
     * line, and so `\s` (all of White_Space on Android's ICU regex, ASCII only
     * on the JVM) reads the same on both.
     */
    private val WHITESPACE = Regex("""[\t\n\u000B\f\r\u0085\p{Z}]""")

    /**
     * The other controls. Replaced by U+FFFD, not a space: a space would end
     * a query or URL run early and print its tail as plain text.
     */
    private val CONTROL = Regex("""\p{Cc}""")

    /**
     * Escapes left encoded. Letters, digits and `-._~` are RFC 3986
     * unreserved: decoded next to an identifier, one would read as part of
     * it and hide it from the matcher's boundary check. `"'()<>` end a [URL]
     * or [QUERY_OR_FRAGMENT] match (as do whitespace and controls, outside
     * the decoded range anyway), so decoding one would split a token and let
     * its tail escape.
     */
    private const val KEPT_ENCODED = "-._~\"'()<>"

    private val MAC_SEPARATED =
        Regex("""(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])""")
    private val MAC_DOTTED = Regex("""(?i)(?<![0-9a-f.])[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}(?![0-9a-f.])""")

    /**
     * A bare 12-hex MAC, only right after a mac-like key, so ordinary hex ids
     * are left alone. The key must not follow an ASCII word character (a
     * lookbehind, so a non-ASCII letter before it counts as a boundary on ICU
     * too). `[\s]*`, not `\s*`: ICU pushes a backtrack frame per character of
     * a bare `\s*` and overflows its stack on a few hundred thousand spaces in
     * a gateway's text, but runs a bracketed class in constant stack.
     */
    private val MAC_KEYED =
        Regex("""(?i)(?<![A-Za-z0-9_])((?:user_)?mac(?:_address)?|ma)([\s]*[=:][\s]*)[0-9a-f]{12}(?![0-9a-f])""")

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

    /** [text] on one line, with recognised URLs reduced to their origin and identifiers masked everywhere else. */
    fun redact(text: String): String {
        val flat = CONTROL.replace(WHITESPACE.replace(text, " "), "\uFFFD")
        val line = maskMacs(QUERY_OR_FRAGMENT.replace(decodeDelimiterEscapes(flat), ""))
        val out = StringBuilder()
        var last = 0
        for (m in URL.findAll(line)) {
            val (scheme, maskedScheme, authority) = m.destructured
            out.append(maskIps(line.substring(last, m.range.first)))
            out.append(if (maskedScheme.isEmpty()) origin("$scheme://$authority") else maskedSchemeOrigin(authority))
            last = m.range.last + 1
        }
        out.append(maskIps(line.substring(last)))
        return out.toString()
    }

    /** `[mac]://host`: a URL whose scheme was a MAC, its host still reduced through [origin]. */
    private fun maskedSchemeOrigin(authority: String): String {
        val reduced = origin("x://$authority")
        return if (reduced.startsWith("x:")) "[mac]" + reduced.removePrefix("x") else reduced
    }

    /**
     * [text] with escapes of printable ASCII delimiters decoded to a fixed
     * point, so nested encoding (`%253A`) is caught, in one linear pass: each
     * decoded character is checked again against the two before it. Escapes
     * cannot overlap (`%` is not a hex digit), so this matches decoding pass
     * after pass, without the quadratic cost on deep nesting. Stray `%`,
     * non-ASCII escapes and [KEPT_ENCODED] are left as they are.
     */
    private fun decodeDelimiterEscapes(text: String): String {
        val out = StringBuilder(text.length)
        for (ch in text) {
            out.append(ch)
            while (true) {
                val decoded = decodableEscapeAtEnd(out) ?: break
                out.setLength(out.length - 3)
                out.append(decoded)
            }
        }
        return out.toString()
    }

    /** The delimiter a `%XY` at the end of [s] stands for, or null if there is none to decode. */
    private fun decodableEscapeAtEnd(s: CharSequence): Char? {
        if (s.length < 3 || s[s.length - 3] != '%') return null
        val hi = asciiHexValue(s[s.length - 2])
        val lo = asciiHexValue(s[s.length - 1])
        if (hi < 0 || lo < 0) return null
        val c = (hi * 16 + lo).toChar()
        return c.takeIf { it in '!'..'~' && !it.isLetterOrDigit() && it !in KEPT_ENCODED }
    }

    private fun asciiHexValue(c: Char): Int = when (c) {
        in '0'..'9' -> c - '0'
        in 'a'..'f' -> c - 'a' + 10
        in 'A'..'F' -> c - 'A' + 10
        else -> -1
    }

    /** Across the whole line, URLs included: a MAC is never a real scheme or host. */
    private fun maskMacs(text: String): String {
        val keyed = MAC_KEYED.replace(text) { "${it.groupValues[1]}${it.groupValues[2]}[mac]" }
        return MAC_DOTTED.replace(MAC_SEPARATED.replace(keyed, "[mac]"), "[mac]")
    }

    /** Outside URLs only: an IP that is a URL's host is kept. */
    private fun maskIps(text: String): String =
        DiagnosticsBundle.IPV4.replace(DiagnosticsBundle.IPV6.replace(text, "[ip]"), "[ip]")
}
