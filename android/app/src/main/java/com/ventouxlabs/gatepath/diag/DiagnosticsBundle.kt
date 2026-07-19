package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.audit.AuditEntry
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
 * `packaging/collect-diagnostics.sh --redact` scrubs — SSID, gateway IP, and
 * portal domain. Applied in two passes so an identifier can't slip through a
 * free-text field:
 * 1. **Audit entries** are scrubbed object-level ([redactEntry]) — a `null`
 *    identifier stays `null` (nothing to reveal), matching the desktop sed which
 *    only rewrites quoted string values; [AuditEntry.portalDomain] is always a
 *    string, so it is always replaced.
 * 2. The **diagnosis render** is scrubbed of any identifier we know from the
 *    audit log (a probe error can embed the portal domain, e.g.
 *    `UnknownHostException: portal.example.com`) and has bare IP literals masked
 *    (gateway/DNS answers the probes echo verbatim). See [redactDiagnosisText].
 */
object DiagnosticsBundle {

    /** Replacement token for scrubbed values — matches the desktop script. */
    const val REDACTED = "REDACTED"

    // Same Json config as AuditLogWriter so re-serialized lines are byte-for-byte
    // the audit.jsonl schema a reader would expect.
    private val json = Json { encodeDefaults = true }

    // Bare IPv4 literal — probe errors / DNS answers echo these verbatim.
    private val IPV4 = Regex("""\b(?:\d{1,3}\.){3}\d{1,3}\b""")

    fun build(
        meta: BundleMeta,
        entries: List<AuditEntry>,
        diagnosis: DiagnosisResult?,
        redact: Boolean,
    ): String = buildString {
        appendLine("=== Gatepath diagnostics ===")
        appendLine("generated_utc: ${meta.generatedUtc}")
        appendLine("app_version: ${meta.appVersionName} (${meta.appVersionCode})")
        appendLine("android: ${meta.androidRelease} (API ${meta.androidSdkInt})")
        appendLine("redacted: $redact")
        appendLine("audit_entries: ${entries.size}")
        appendLine()

        appendLine("--- Latest diagnosis ---")
        val diagText = renderDiagnosis(diagnosis)
        appendLine(if (redact) redactDiagnosisText(diagText, entries) else diagText)
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
    }

    private fun redactEntry(entry: AuditEntry): AuditEntry = entry.copy(
        ssid = entry.ssid?.let { REDACTED },
        gatewayIp = entry.gatewayIp?.let { REDACTED },
        portalDomain = REDACTED,
    )

    /**
     * Scrubs the diagnosis free-text so redaction stays honest there too:
     * replaces every identifier we can name from the audit log (longest-first,
     * so a domain isn't half-masked by a substring) and masks bare IP literals.
     */
    private fun redactDiagnosisText(text: String, entries: List<AuditEntry>): String {
        val known = buildSet {
            for (e in entries) {
                e.ssid?.takeIf { it.isNotBlank() }?.let { add(it) }
                e.gatewayIp?.takeIf { it.isNotBlank() }?.let { add(it) }
                e.portalDomain.takeIf { it.isNotBlank() }?.let { add(it) }
            }
        }.sortedByDescending { it.length }

        var out = text
        for (value in known) {
            out = out.replace(value, REDACTED)
        }
        return out.replace(IPV4, REDACTED)
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
