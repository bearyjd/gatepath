package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.audit.AuditEntry
import com.ventouxlabs.gatepath.network.PortalProbeCapture
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/** Metadata header for a diagnostics bundle. Pure data — no android.* deps. */
data class BundleMeta(
    val generatedUtc: String,
    val appVersionName: String,
    val appVersionCode: Long,
    val androidRelease: String,
    val androidSdkInt: Int,
)

/**
 * Builds the human-readable diagnostics bundle a user shares from the app
 * (audit log + latest [DiagnosisResult]).
 *
 * Deliberately pure — no `android.*` imports — so the bundle assembly and, more
 * importantly, the redaction contract are exercised by the no-Android-SDK JVM
 * test suite (run-jvm-tests.sh). The Android glue that reads the real audit
 * file, gathers [BundleMeta], writes the file and fires `ACTION_SEND` lives in
 * `com.ventouxlabs.gatepath.share.DiagnosticsSharer`.
 *
 * ### Redaction (`redact = true`)
 * Scope: the network-identifying fields the desktop
 * `gatepath-netns-helper/packaging/collect-diagnostics.sh --redact` scrubs —
 * SSID, gateway IP, and portal domain — plus the certificate fingerprint and
 * validity window in the evidence section (see point 3). Applied in passes so
 * an identifier can't slip through a free-text field:
 * 1. **Audit entries** are scrubbed object-level ([redactEntry]) — a `null`
 *    identifier stays `null` (nothing to reveal), matching the desktop sed which
 *    only rewrites quoted string values; [AuditEntry.portalDomain] is always a
 *    string, so it is always replaced.
 * 2. The **diagnosis, evidence and console renders** are scrubbed of any
 *    identifier we know — from the audit log *and* from the current
 *    [IncidentEvidence]'s resolver answers, [IncidentEvidence.resolverWifi] /
 *    [IncidentEvidence.resolverDoh] — and have bare IP literals masked
 *    unconditionally. The evidence-sourced half matters because a Tunnelled,
 *    Blocked, DnsStrict, or Unknown incident never opens a session and so
 *    never writes an audit entry: without it, a hostname the resolver
 *    answered with — and that the same incident's [IncidentEvidence.bindError]
 *    or [IncidentEvidence.fallbackError] can independently echo, e.g.
 *    `UnknownHostException: portal.example.com` — would have nothing to match
 *    against and would leak. See [redactDiagnosisText].
 * 3. **Certificate fields** in the evidence section are redacted structurally,
 *    not by text substitution: [CertSummary.sha256Fingerprint] and the two
 *    validity epochs are replaced outright under `redact = true`, because a
 *    venue's certificate fingerprint and validity window identify it about as
 *    precisely as its SSID. [CertSummary.primaryError] and
 *    [CertSummary.selfSigned] are kept — low-cardinality and diagnostically
 *    essential. See [renderEvidence].
 *
 * The **probe capture** needs no pass of its own: [PortalProbeCapture] only
 * admits values that are safe to share, enforced at construction rather than
 * scrubbed here.
 */
object DiagnosticsBundle {

    /** Replacement token for scrubbed values — matches the desktop script. */
    const val REDACTED = "REDACTED"

    // Same Json config as AuditLogWriter so re-serialized lines are byte-for-byte
    // the audit.jsonl schema a reader would expect.
    private val json = Json { encodeDefaults = true }

    // Bare IPv4 literal — probe errors / DNS answers echo these verbatim.
    private val IPV4 = Regex("""\b(?:\d{1,3}\.){3}\d{1,3}\b""")

    // Bare IPv6 literal — resolver answers can be v6 too. Grammar-accurate: a
    // full form of exactly eight 1-to-4-hex groups, or a compressed form that
    // contains `::`. Lookarounds (rather than \b) reject a match glued to a
    // surrounding hex/colon/dot/hyphen run. A timestamp like `12:34:56` or
    // `T00:00:00Z` has neither eight groups nor `::`, so it can't match.
    private val IPV6 = Regex(
        """(?<![0-9A-Za-z:.-])(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}""" +
            """|(?:[0-9a-fA-F]{1,4}:){1,7}:(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6})?""" +
            """|::(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6})?)(?![0-9A-Za-z:.-])""",
    )

    fun build(
        meta: BundleMeta,
        entries: List<AuditEntry>,
        diagnosis: DiagnosisResult?,
        probeCapture: PortalProbeCapture? = null,
        evidence: IncidentEvidence? = null,
        unreadableEntries: Int = 0,
        consoleEntries: List<ConsoleCaptureEntry> = emptyList(),
        consoleUnreadable: Int = 0,
        redact: Boolean,
    ): String = buildString {
        appendLine("=== Gatepath diagnostics ===")
        appendLine("generated_utc: ${meta.generatedUtc}")
        appendLine("app_version: ${meta.appVersionName} (${meta.appVersionCode})")
        appendLine("android: ${meta.androidRelease} (API ${meta.androidSdkInt})")
        appendLine("redacted: $redact")
        appendLine("audit_entries: ${entries.size}")
        // Silence here would let a reader mistake a dropped line for an event
        // that never happened, so an incomplete log says so on its face.
        if (unreadableEntries > 0) {
            appendLine("audit_entries_unreadable: $unreadableEntries")
        }
        appendLine()

        appendLine("--- Latest diagnosis ---")
        val diagText = renderDiagnosis(diagnosis)
        appendLine(if (redact) redactDiagnosisText(diagText, entries, evidence) else diagText)
        appendLine()

        appendLine("--- Latest portal probe capture ---")
        appendLine(renderProbeCapture(probeCapture))
        appendLine()

        appendLine("--- Incident evidence ---")
        val evidenceText = renderEvidence(evidence, redact)
        appendLine(if (redact) redactDiagnosisText(evidenceText, entries, evidence) else evidenceText)
        appendLine()

        appendLine("--- Audit log (audit.jsonl) ---")
        if (entries.isEmpty()) {
            appendLine("(no entries)")
        } else {
            for (entry in entries) {
                val e = if (redact) redactEntry(entry) else entry
                appendLine(json.encodeToString(e))
            }
        }
        appendLine()

        appendLine("--- WebView console (most recent capture) ---")
        if (consoleEntries.isEmpty()) {
            appendLine("(no console messages captured)")
        } else {
            if (consoleUnreadable > 0) {
                appendLine("console_messages_unreadable: $consoleUnreadable")
            }
            for (c in consoleEntries) {
                val line = "[${c.level}] ${c.sourceHost}:${c.lineNumber} ${c.message}"
                appendLine(if (redact) redactConsoleText(line, entries, evidence) else line)
            }
        }
    }

    // Session tokens, API keys, and hashes a portal's JS might accidentally
    // log have no fixed shape, unlike the known identifiers scrubbed below —
    // this is a best-effort heuristic, not a guarantee (see SECURITY_MODEL.md
    // "Off-device diagnostics: WebView console capture"). JWT is applied
    // first so its three segments collapse to one REDACTED token instead of
    // three separate ones joined by dots.
    private val JWT = Regex("""\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b""")
    private val LONG_TOKEN = Regex("""\b[A-Za-z0-9_-]{20,}\b""")

    /**
     * Console text gets the known-identifier/IPv4 scrub first, then the
     * generic token pass. Order matters: LONG_TOKEN matches any 20+ char
     * alphanumeric run, so running it first could eat *part* of a known
     * identifier (e.g. a long portal subdomain) before the known-identifier
     * pass gets a chance to recognize and mask the whole thing, leaking a
     * fragment. Running known-identifier first is safe because its
     * replacement is the literal "REDACTED" (8 chars), below the 20-char
     * LONG_TOKEN threshold, so it isn't re-matched by the generic pass.
     */
    private fun redactConsoleText(text: String, entries: List<AuditEntry>, evidence: IncidentEvidence?): String {
        val knownMasked = redactDiagnosisText(text, entries, evidence)
        return knownMasked.replace(JWT, REDACTED).replace(LONG_TOKEN, REDACTED)
    }

    private fun redactEntry(entry: AuditEntry): AuditEntry = entry.copy(
        ssid = entry.ssid?.let { REDACTED },
        gatewayIp = entry.gatewayIp?.let { REDACTED },
        portalDomain = REDACTED,
    )

    /**
     * Scrubs the diagnosis free-text so redaction stays honest there too:
     * replaces every identifier we can name — from the audit log *and* from
     * the current incident's [IncidentEvidence] — (longest-first, so a domain
     * isn't half-masked by a substring) and masks bare IP literals.
     *
     * The evidence-sourced half is not optional: a Tunnelled, Blocked,
     * DnsStrict, or Unknown incident never opens a session and so never
     * writes an audit entry, leaving `entries` empty. Without also reading
     * [IncidentEvidence.resolverWifi] / [IncidentEvidence.resolverDoh], a
     * resolver-answer hostname that [IncidentEvidence.bindError] or
     * [IncidentEvidence.fallbackError] independently echoes would have
     * nothing in `known` to match against and would leak.
     */
    private fun redactDiagnosisText(text: String, entries: List<AuditEntry>, evidence: IncidentEvidence? = null): String {
        val known = buildSet {
            for (e in entries) {
                e.ssid?.takeIf { it.isNotBlank() }?.let { add(it) }
                e.gatewayIp?.takeIf { it.isNotBlank() }?.let { add(it) }
                e.portalDomain.takeIf { it.isNotBlank() }?.let { add(it) }
            }
            evidence?.resolverWifi?.forEach { it.takeIf { v -> v.isNotBlank() }?.let { add(it) } }
            evidence?.resolverDoh?.forEach { it.takeIf { v -> v.isNotBlank() }?.let { add(it) } }
        }.sortedByDescending { it.length }

        var out = text
        for (value in known) {
            out = out.replace(value, REDACTED)
        }
        return out.replace(IPV4, REDACTED).replace(IPV6, REDACTED)
    }

    private fun renderDiagnosis(diagnosis: DiagnosisResult?): String {
        if (diagnosis == null) return "(no diagnosis captured)"
        return buildString {
            appendLine("top_finding: ${renderReport(diagnosis.top)}")
            appendLine("recommended_action: ${renderAction(diagnosis.recommended)}")
            append("all_findings:")
            for (check in diagnosis.checks) {
                append("\n  - ${check.probeName}: ${renderReport(check.report)}")
            }
        }
    }

    /**
     * No redaction branch, deliberately: [PortalProbeCapture] only admits
     * values that are safe to share, so there is nothing here to scrub. It
     * carries no body-derived field for the same reason — a gateway controls
     * its own response, so a length or a digest is an identifier channel it
     * can vary per device.
     */
    private fun renderProbeCapture(capture: PortalProbeCapture?): String =
        renderCaptureLines(capture, prefix = "")

    /**
     * Shared by [renderProbeCapture] and [renderEvidence] so the standalone
     * "latest capture" section and the capture embedded per-incident render
     * identically, just under a different key prefix.
     */
    private fun renderCaptureLines(capture: PortalProbeCapture?, prefix: String): String {
        if (capture == null) return "${prefix}capture: (no intercepted response captured)"
        return buildString {
            appendLine("${prefix}http_status: ${capture.httpStatus}")
            appendLine("${prefix}content_type: ${capture.contentType ?: "(absent)"}")
            append("${prefix}redirect_signal: ${capture.redirectSignal}")
        }
    }

    private fun renderEvidence(e: IncidentEvidence?, redact: Boolean): String {
        if (e == null) return "(no incident evidence captured)"
        return buildString {
            appendLine("confinement: ${e.confinement}")
            appendLine("probe_path: ${e.probePath}")
            appendLine("vpn_kind: ${e.vpnKind}")
            appendLine("vpn_interfaces: ${if (e.vpnInterfaces.isEmpty()) "(none)" else e.vpnInterfaces.joinToString(", ")}")
            appendLine("private_dns_strict: ${e.privateDnsStrict}")
            appendLine(renderCaptureLines(e.probeCapture, prefix = "probe_"))
            appendLine("resolver_wifi: ${if (e.resolverWifi.isEmpty()) "(none)" else e.resolverWifi.joinToString(", ")}")
            appendLine("resolver_doh: ${if (e.resolverDoh.isEmpty()) "(none)" else e.resolverDoh.joinToString(", ")}")
            val c = e.certSummary
            if (c == null) {
                appendLine("cert: (no certificate error observed)")
            } else {
                // primaryError and selfSigned are low-cardinality and
                // diagnostically essential, so they survive redaction; the
                // fingerprint and validity window identify the venue about as
                // precisely as its SSID, so both are replaced outright rather
                // than left to a text-substitution pass over the render.
                appendLine("cert_primary_error: ${c.primaryError}")
                appendLine("cert_not_before_epoch_ms: ${renderCertEpoch(c.notBeforeEpochMillis, redact)}")
                appendLine("cert_not_after_epoch_ms: ${renderCertEpoch(c.notAfterEpochMillis, redact)}")
                appendLine("cert_self_signed: ${c.selfSigned}")
                appendLine("cert_sha256: ${renderCertFingerprint(c.sha256Fingerprint, redact)}")
            }
            appendLine("bind_error: ${e.bindError ?: "(none)"}")
            append("fallback_error: ${e.fallbackError ?: "(none)"}")
        }
    }

    /** An absent epoch has nothing to reveal, so it stays `(absent)` even under redaction. */
    private fun renderCertEpoch(epochMillis: Long?, redact: Boolean): String = when {
        epochMillis == null -> "(absent)"
        redact -> REDACTED
        else -> epochMillis.toString()
    }

    /** An absent fingerprint has nothing to reveal, so it stays `(absent)` even under redaction. */
    private fun renderCertFingerprint(fingerprint: String, redact: Boolean): String = when {
        fingerprint.isEmpty() -> "(absent)"
        redact -> REDACTED
        else -> fingerprint
    }

    private fun renderReport(r: DiagnosticReport): String = when (r) {
        is DiagnosticReport.Healthy ->
            "Healthy"
        is DiagnosticReport.VpnBlocking ->
            "VpnBlocking(interface=${r.interfaceName}, fullTunnel=${r.isFullTunnel})"
        is DiagnosticReport.DnsHijack ->
            "DnsHijack(host=${r.hostProbed}, system=${r.systemAnswer}, doh=${r.doHAnswer})"
        is DiagnosticReport.PrivateDnsBlocking ->
            "PrivateDnsBlocking(resolver=${r.resolverHost ?: "auto"})"
        is DiagnosticReport.HttpProxyBlocking ->
            "HttpProxyBlocking(${r.description})"
        is DiagnosticReport.SandboxedWebView ->
            "SandboxedWebView(code=${r.errorCode}, desc=${r.errorDescription})"
        is DiagnosticReport.HttpsOnlyCaptive ->
            "HttpsOnlyCaptive(${r.httpsErrorMessage})"
        is DiagnosticReport.CellularFallback ->
            "CellularFallback(validated=${r.cellularValidated})"
        is DiagnosticReport.NoDnsServers ->
            "NoDnsServers"
        is DiagnosticReport.PortalRedirectLoop ->
            "PortalRedirectLoop(chain=${r.chain.joinToString(" -> ")})"
        is DiagnosticReport.ClockSkew ->
            "ClockSkew(skewSeconds=${r.skewSeconds})"
        is DiagnosticReport.Inconclusive ->
            "Inconclusive(errors=${r.probeErrors.joinToString("; ")})"
    }

    private fun renderAction(a: RecommendedAction): String = when (a) {
        is RecommendedAction.NoActionAvailable -> "none"
        is RecommendedAction.UserAction -> "[${a.id}] ${a.instruction}"
    }
}
