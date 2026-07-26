package com.ventouxlabs.gatepath.ui

/**
 * Pure, JVM-testable policy for [GatepathWebView]'s `onReceivedSslError`.
 *
 * A captive gateway's own login page routinely fails certificate validation —
 * self-signed, expired, or a CN that is the gateway's RFC1918 IP. The
 * unoverridden `WebViewClient` default is `handler.cancel()`, which aborts the
 * load with **no** `onReceivedError` callback and no visible message, so the
 * portal just white-screens. Proceeding past those errors is what makes the
 * portal usable at all.
 *
 * But this WebView is **not confined to the portal host**: since off-domain
 * navigations were unblocked for captive-vendor compatibility (see
 * [WebViewHostMatching] and `GatepathWebView.shouldOverrideUrlLoading`), the
 * gateway can steer the page to any host it likes. Proceeding
 * unconditionally would therefore disable certificate validation for arbitrary
 * hosts on the one network where an attacker is on-path by definition — a
 * hostile gateway could redirect to a real identity provider and MITM it with
 * a self-signed cert, silently. So the bypass is scoped: **the portal host and
 * its subdomains only; everything else keeps normal TLS enforcement.**
 *
 * Match rule is [WebViewHostMatching.isSameOriginHost], which fails closed on
 * a blank portal host or a blank error host — i.e. when we can't tell, we
 * don't bypass.
 */
object SslErrorPolicy {

    /**
     * @param errorHost host of the URL that raised the certificate error
     * @param portalHost host parsed out of the captive-portal URL
     * @return true to call `handler.proceed()`, false to `handler.cancel()`
     */
    fun shouldProceed(errorHost: String, portalHost: String): Boolean =
        WebViewHostMatching.isSameOriginHost(errorHost, portalHost)
}
