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
import java.io.ByteArrayInputStream
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
                domStorageEnabled = false
                databaseEnabled = false
                @Suppress("DEPRECATION")
                saveFormData = false
                cacheMode = WebSettings.LOAD_NO_CACHE
            }
            CookieManager.getInstance().setAcceptCookie(false)
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
        return if (isSameOrigin) {
            false // allow WebView to load it
        } else {
            Log.d(TAG, "Blocking off-domain navigation to ${request.url.forLog()} (portal host=$portalHost)")
            onBlockedNavigation()
            true // blocked
        }
    }

    override fun shouldInterceptRequest(
        view: WebView,
        request: WebResourceRequest,
    ): WebResourceResponse? {
        val host = runCatching {
            request.url.host?.lowercase() ?: return null
        }.getOrNull() ?: return null

        return if (BlockedDomains.isBlocked(host)) {
            onBlockedResource()
            emptyResponse()
        } else {
            null
        }
    }

    private fun emptyResponse(): WebResourceResponse =
        WebResourceResponse("text/plain", "utf-8", ByteArrayInputStream(ByteArray(0)))
}
