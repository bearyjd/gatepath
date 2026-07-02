package com.ventouxlabs.gatepath.diag

/**
 * Outcome of a single [DiagnosticProbe].
 *
 * Sealed so the engine + UI can `when`-match exhaustively. Each variant carries
 * the data the UI needs to explain the finding to the user without needing
 * back-references to the probe that produced it.
 */
sealed interface DiagnosticReport {

    /** Probe ran cleanly and saw no problem on its dimension. */
    data object Healthy : DiagnosticReport

    /**
     * The user is on a full-tunnel VPN that blocks captive-portal access.
     * Pausing the VPN is the standard remedy.
     */
    data class VpnBlocking(
        val interfaceName: String,
        val isFullTunnel: Boolean,
    ) : DiagnosticReport

    /**
     * The captive gateway is hijacking DNS for hosts it has no business
     * intercepting (i.e. not just the connectivity-check endpoints). Often a
     * sign of an aggressive captive setup that will also break HTTPS.
     */
    data class DnsHijack(
        val hostProbed: String,
        val systemAnswer: String,
        val doHAnswer: String,
    ) : DiagnosticReport

    /**
     * Android Private DNS (DNS-over-TLS) is active and likely blocking captive
     * sign-in: the resolver can't reach its DoT endpoint until the user signs
     * in, but signing in requires DNS resolution. Classic chicken-and-egg.
     */
    data class PrivateDnsBlocking(
        val resolverHost: String?,
    ) : DiagnosticReport

    /**
     * The bound network has an HTTP proxy configured (PAC or static); the
     * captive-portal redirect is being eaten by the proxy.
     */
    data class HttpProxyBlocking(
        val description: String,
    ) : DiagnosticReport

    /**
     * The captive page is loading (or attempting to) inside a sandboxed WebView
     * subprocess that does NOT inherit `bindProcessToNetwork`. Phase 3.5 ships
     * the `shouldInterceptRequest` bridge that fixes this; for now the report
     * exists so we can field-test the diagnosis.
     */
    data class SandboxedWebView(
        val errorCode: Int,
        val errorDescription: String,
    ) : DiagnosticReport

    /**
     * Captive blocks HTTPS connections outright (cert error or RST). The user's
     * browser may show a warning page; Gatepath should fall back to its HTTP
     * connectivity-check path.
     */
    data class HttpsOnlyCaptive(
        val httpsErrorMessage: String,
    ) : DiagnosticReport

    /**
     * The userspace fallback probe succeeded — but via cellular, not WiFi. The
     * user thinks they're on captive WiFi but cellular is silently picking up
     * traffic, masking the captive condition.
     */
    data class CellularFallback(
        val cellularValidated: Boolean,
    ) : DiagnosticReport

    /**
     * No probe could conclude. Carries the raw probe errors so the user (or
     * a developer reading logs) can see what actually went wrong.
     */
    data class Inconclusive(
        val probeErrors: List<String>,
    ) : DiagnosticReport
}
