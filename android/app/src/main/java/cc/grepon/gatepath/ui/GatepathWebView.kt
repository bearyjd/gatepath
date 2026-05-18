package cc.grepon.gatepath.ui

import android.graphics.Bitmap
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.util.Log
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import cc.grepon.gatepath.BuildConfig
import cc.grepon.gatepath.network.BlockedDomains
import java.net.URI

private const val TAG = "GatepathWebView"

/**
 * Captive portal URLs commonly carry MAC addresses, gateway/WAN IPs, and
 * session IDs in query params (Sky Admin, Cisco Meraki, Aruba, etc.). In
 * release builds we log only the host so those identifiers don't end up in
 * logcat where any app with READ_LOGS on a rooted/dev device could read them.
 * Debug builds get the full URL since visibility is the whole point.
 */
private fun Uri.forLog(): String =
    if (BuildConfig.DEBUG) toString() else (host ?: "(no host)")

private fun String.urlForLog(): String =
    if (BuildConfig.DEBUG) this else runCatching { Uri.parse(this).host ?: "(no host)" }
        .getOrDefault("(unparseable)")

/**
 * Composable that hosts a security-hardened [WebView] bound to the captive-portal [Network].
 *
 * Security guarantees (per docs/SECURITY_MODEL.md):
 * - Traffic bound to [network] via [ConnectivityManager.bindProcessToNetwork].
 * - JavaScript enabled (required for most portal pages); all other risky settings disabled.
 * - Cookies disabled.
 * - Off-domain navigations refused and counted via [onBlockedNavigation].
 * - Tracker/analytics sub-requests blocked via [BlockedDomains] and counted via [onBlockedResource].
 * - Cache and history wiped on dispose.
 */
@Composable
fun GatepathWebView(
    url: String,
    network: Network,
    connectivityManager: ConnectivityManager,
    onBlockedNavigation: () -> Unit,
    onBlockedResource: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val portalHost = remember(url) { runCatching { URI(url).host }.getOrNull() ?: "" }

    val webView = remember {
        WebView(context).apply {
            settings.apply {
                javaScriptEnabled = true
                allowFileAccess = false
                allowContentAccess = false
                // domStorageEnabled is REQUIRED by many captive portals
                // (Cisco Meraki, Aruba, Sky Admin) — they stash session
                // nonces in sessionStorage/localStorage during the
                // sign-in flow. Cleared on dispose below.
                domStorageEnabled = true
                // databaseEnabled / saveFormData: deprecated no-ops (WebSQL removed
                // from Chromium in API 33; saveFormData superseded by autofill in
                // API 26). Kept set to false to preserve the explicit-defaults intent.
                @Suppress("DEPRECATION")
                databaseEnabled = false
                @Suppress("DEPRECATION")
                saveFormData = false
                cacheMode = WebSettings.LOAD_NO_CACHE
                // Many captive portals (Meraki splash flows in particular)
                // call window.open(...) from the Continue handler. With
                // javaScriptCanOpenWindowsAutomatically=true and
                // setSupportMultipleWindows=false (the WebView default),
                // window.open replaces the current page — which is what
                // the captive flow expects.
                //
                // Setting setSupportMultipleWindows=true would route every
                // window.open through WebChromeClient.onCreateWindow; if
                // that's not overridden the default returns false and the
                // new window is silently blocked. Don't set it without
                // adding the handler.
                javaScriptCanOpenWindowsAutomatically = true
                setSupportMultipleWindows(false)
                // Captive splash pages frequently mix HTTP and HTTPS
                // resources (the splash is HTTPS, included scripts are
                // sometimes HTTP). Default NEVER_ALLOW silently drops
                // those scripts → page renders but JS handlers don't
                // bind. COMPATIBILITY_MODE matches stock browser.
                mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            }
            // Cookies are REQUIRED for almost every captive portal —
            // sign-in pages set a session cookie on the redirect, then
            // expect it back on form submit. Disabling cookies caused
            // sign-in attempts to fail with the portal's own
            // "bad request — error parsing required information" 4xx
            // (server side couldn't find the session). We clear all
            // cookies on dispose so nothing persists past the session.
            val cookieManager = CookieManager.getInstance()
            cookieManager.setAcceptCookie(true)
            cookieManager.setAcceptThirdPartyCookies(this, true)
            webViewClient = buildWebViewClient(portalHost, onBlockedNavigation, onBlockedResource)
            // Diagnostic-only WebChromeClient: forward console.log / console.error
            // from the captive portal page into logcat. Captive portal sign-in
            // pages frequently break in unexpected ways (CSP, missing JS frameworks,
            // sandbox issues) — without this we're flying blind.
            //
            // In release builds we drop the message body and source URL since
            // portal-page JS sometimes echoes tokens or session data; we keep
            // only the level + line number, which is enough to know "the page
            // logged an error at line 42" without revealing what it said.
            webChromeClient = object : WebChromeClient() {
                override fun onConsoleMessage(msg: ConsoleMessage): Boolean {
                    val level = when (msg.messageLevel()) {
                        ConsoleMessage.MessageLevel.ERROR -> Log.ERROR
                        ConsoleMessage.MessageLevel.WARNING -> Log.WARN
                        else -> Log.DEBUG
                    }
                    val detail = if (BuildConfig.DEBUG) {
                        "${msg.sourceId()}:${msg.lineNumber()} ${msg.message()}"
                    } else {
                        "(line ${msg.lineNumber()})"
                    }
                    Log.println(level, TAG, "console: [${msg.messageLevel()}] $detail")
                    return true
                }
            }
        }
    }

    DisposableEffect(network) {
        connectivityManager.bindProcessToNetwork(network)
        webView.loadUrl(url)

        onDispose {
            connectivityManager.bindProcessToNetwork(null)
            webView.clearCache(true)
            webView.clearHistory()
            // Clear the session-scoped state we enabled for the portal
            // sign-in: cookies (set by the captive page) and DOM storage
            // (sessionStorage / localStorage). Both flushed so nothing
            // from the portal persists past this session.
            CookieManager.getInstance().removeAllCookies(null)
            CookieManager.getInstance().flush()
            webView.clearFormData()
            WebStorage.getInstance().deleteAllData()
        }
    }

    AndroidView(factory = { webView }, modifier = modifier)
}

private fun buildWebViewClient(
    portalHost: String,
    onBlockedNavigation: () -> Unit,
    onBlockedResource: () -> Unit,
): WebViewClient = object : WebViewClient() {

    override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
        Log.d(TAG, "Page started: ${url.urlForLog()}")
    }

    override fun onPageFinished(view: WebView, url: String) {
        Log.d(TAG, "Page finished: ${url.urlForLog()}")
    }

    override fun onReceivedError(
        view: WebView,
        request: WebResourceRequest,
        error: WebResourceError,
    ) {
        // Without this log, captive-portal load failures (DNS, EPERM via the
        // sandboxed WebView process, TLS cert error, redirect loop, etc.)
        // silently produce a blank page.
        Log.w(
            TAG,
            "onReceivedError ${request.url.forLog()}: code=${error.errorCode} desc=${error.description} " +
                "isMainFrame=${request.isForMainFrame}",
        )
    }

    override fun onReceivedHttpError(
        view: WebView,
        request: WebResourceRequest,
        errorResponse: WebResourceResponse,
    ) {
        Log.w(
            TAG,
            "onReceivedHttpError ${request.url.forLog()}: status=${errorResponse.statusCode} " +
                "reason=${errorResponse.reasonPhrase} isMainFrame=${request.isForMainFrame}",
        )
    }

    override fun shouldOverrideUrlLoading(
        view: WebView,
        request: WebResourceRequest,
    ): Boolean {
        val requestHost = runCatching { request.url.host ?: "" }.getOrDefault("")
        val isSameOrigin = WebViewHostMatching.isSameOriginHost(requestHost, portalHost)
        // Cisco Meraki, UniFi, Cisco ISE, and several other captive
        // vendors POST the "Continue" / sign-in form to a backend on a
        // DIFFERENT host than the splash page (eg splash on the AP IP,
        // grant POST to n143.network-auth.com). Hard-blocking off-domain
        // navigation cancelled those form submits → user saw "nothing
        // happens" on Continue. Stock Android captive handler allows
        // these navigations; we now match that.
        //
        // We still count off-domain navigations so the audit log records
        // them, but we let the WebView follow them. Subresource trackers
        // are still blocked via shouldInterceptRequest below — that's
        // the layer that does the real privacy work.
        if (!isSameOrigin) {
            Log.d(
                TAG,
                "Off-domain main-frame navigation to ${request.url.forLog()} (portal host=$portalHost) — allowing for captive flow",
            )
            onBlockedNavigation()
        }
        return false // always let the WebView load it
    }

    override fun shouldInterceptRequest(
        view: WebView,
        request: WebResourceRequest,
    ): WebResourceResponse? {
        val host = runCatching {
            request.url.host?.lowercase() ?: return null
        }.getOrNull() ?: return null

        // Meraki, Aruba, Sky and many other captive splash pages embed
        // Google Analytics / Tag Manager. Returning an empty 200 for
        // those scripts caused the page's inline init script to throw
        // on the first `gtag(...)` call (ReferenceError), which killed
        // ALL subsequent JS in that <script> block — including the
        // Continue button's click-handler binding. Result: tap did
        // nothing. Stock Android captive handler doesn't intercept
        // these requests at all and works.
        //
        // We log + count the request so the audit log records what
        // would have been blocked, but we let the WebView load it. The
        // captive session is short-lived and we clear cookies +
        // WebStorage on dispose, so persistent tracking is bounded to
        // the sign-in flow.
        if (BlockedDomains.isBlocked(host)) {
            Log.d(TAG, "Tracker subresource ${request.url.forLog()} — allowing for captive flow")
            onBlockedResource()
        }
        return null
    }
}
