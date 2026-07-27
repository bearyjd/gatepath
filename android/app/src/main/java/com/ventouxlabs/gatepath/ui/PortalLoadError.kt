package com.ventouxlabs.gatepath.ui

/**
 * Pure, JVM-testable model for "the portal page did not load".
 *
 * Every load failure in [GatepathWebView] previously rendered identically: a
 * blank white page, with the only trace a `Log.w` line the user can't see.
 * That covers cert errors, DNS failures, EPERM from the sandboxed WebView
 * process, redirect loops — all of them. This type turns a failure into
 * something the UI can show.
 *
 * Deliberately free of `android.*` imports so the classification and copy can
 * be regression-tested on a plain JVM (same reason as [WebViewHostMatching]
 * and [SslErrorPolicy]). The WebView error constants are duplicated as
 * literals below rather than imported — see [PortalLoadErrorKind.fromWebViewErrorCode].
 */
data class PortalLoadError(
    val kind: PortalLoadErrorKind,
    /**
     * Host of the failed URL, or blank if it couldn't be parsed. Host only,
     * never the full URL: portal URLs routinely carry MAC addresses, gateway
     * IPs and session tokens in query params (same reasoning as the
     * release-build logging policy in [GatepathWebView]).
     */
    val host: String,
    /** Raw code/description for the log line. Never shown to the user. */
    val technicalDetail: String,
)

enum class PortalLoadErrorKind {
    /** Certificate error on a host outside the portal's origin — refused by [SslErrorPolicy]. */
    CERT_REJECTED,

    /** Name resolution failed. Common when the portal hands out a hostname only it can resolve. */
    HOST_LOOKUP_FAILED,

    /** TCP connect / IO failure or timeout. */
    UNREACHABLE,

    /** The gateway bounced the request around until the WebView gave up. */
    REDIRECT_LOOP,

    /** TLS handshake failed outright (not a cert-trust decision — no page to proceed to). */
    TLS_HANDSHAKE_FAILED,

    /** Anything else, including the WebView's own ERROR_UNKNOWN. */
    UNKNOWN,
    ;

    companion object {
        /**
         * Map a `WebViewClient.ERROR_*` code to a kind.
         *
         * The constants are inlined as literals so this file stays
         * `android.*`-free and testable on the JVM. They are stable public API
         * (`android.webkit.WebViewClient`) and have not changed since API 1:
         *  -2 ERROR_HOST_LOOKUP, -4 ERROR_AUTHENTICATION, -6 ERROR_CONNECT,
         *  -7 ERROR_IO, -8 ERROR_TIMEOUT, -9 ERROR_REDIRECT_LOOP,
         *  -11 ERROR_FAILED_SSL_HANDSHAKE.
         */
        fun fromWebViewErrorCode(code: Int): PortalLoadErrorKind = when (code) {
            -2 -> HOST_LOOKUP_FAILED
            -6, -7, -8 -> UNREACHABLE
            -9 -> REDIRECT_LOOP
            -11 -> TLS_HANDSHAKE_FAILED
            else -> UNKNOWN
        }
    }
}

/**
 * User-facing copy for a [PortalLoadError].
 *
 * Rules this copy follows:
 *  - Say what failed and what the user can do. "Something went wrong" is what
 *    we're replacing, so it is not an acceptable fallback.
 *  - Never blame the user's device for a gateway problem — captive gateways
 *    are the usual culprit and the wording should point there.
 *  - Host is included only when non-blank, and it is a host, never a full URL.
 */
object PortalLoadErrorText {

    fun title(kind: PortalLoadErrorKind): String = when (kind) {
        PortalLoadErrorKind.CERT_REJECTED -> "Sign-in page blocked for safety"
        PortalLoadErrorKind.HOST_LOOKUP_FAILED -> "Couldn't find the sign-in page"
        PortalLoadErrorKind.UNREACHABLE -> "Couldn't reach the sign-in page"
        PortalLoadErrorKind.REDIRECT_LOOP -> "The network kept redirecting"
        PortalLoadErrorKind.TLS_HANDSHAKE_FAILED -> "Secure connection failed"
        PortalLoadErrorKind.UNKNOWN -> "The sign-in page didn't load"
    }

    fun body(error: PortalLoadError): String {
        val where = if (error.host.isBlank()) "this network's sign-in page" else error.host
        return when (error.kind) {
            PortalLoadErrorKind.CERT_REJECTED ->
                "$where sent an invalid security certificate, and it isn't the " +
                    "network's own sign-in page — so Gatepath refused to load it. " +
                    "This can mean the network is tampering with traffic. " +
                    "Avoid signing in here."

            PortalLoadErrorKind.HOST_LOOKUP_FAILED ->
                "Gatepath couldn't look up $where. The network may still be " +
                    "setting up, or its DNS may be misconfigured. Try again in a moment."

            PortalLoadErrorKind.UNREACHABLE ->
                "Gatepath reached the network but $where didn't respond. " +
                    "The gateway may be busy or offline. Try again."

            PortalLoadErrorKind.REDIRECT_LOOP ->
                "$where redirected in a loop and never settled on a page. " +
                    "This is usually a fault in the network's sign-in system. " +
                    "Try again, or use the network's own sign-in screen."

            PortalLoadErrorKind.TLS_HANDSHAKE_FAILED ->
                "Gatepath couldn't establish a secure connection to $where. " +
                    "The gateway may not support a compatible encryption standard."

            PortalLoadErrorKind.UNKNOWN ->
                "Gatepath couldn't load $where and the network didn't say why. " +
                    "Try again, or run diagnostics to look closer."
        }
    }

    /** Whether a retry has any chance of helping. Drives the Try-again button. */
    fun isRetryable(kind: PortalLoadErrorKind): Boolean = when (kind) {
        // Retrying a rejected certificate just re-rejects it, and inviting the
        // user to retry a possible tampering signal is the wrong nudge.
        PortalLoadErrorKind.CERT_REJECTED -> false
        else -> true
    }
}
