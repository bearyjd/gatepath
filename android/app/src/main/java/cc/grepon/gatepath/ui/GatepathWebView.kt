package cc.grepon.gatepath.ui

import android.net.ConnectivityManager
import android.net.Network
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
import cc.grepon.gatepath.network.BlockedDomains
import java.io.ByteArrayInputStream
import java.net.URI

private const val TAG = "GatepathWebView"

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
            webChromeClient = object : WebChromeClient() {
                override fun onConsoleMessage(msg: ConsoleMessage): Boolean {
                    val level = when (msg.messageLevel()) {
                        ConsoleMessage.MessageLevel.ERROR -> Log.ERROR
                        ConsoleMessage.MessageLevel.WARNING -> Log.WARN
                        else -> Log.DEBUG
                    }
                    Log.println(
                        level,
                        TAG,
                        "console: [${msg.messageLevel()}] ${msg.sourceId()}:${msg.lineNumber()} ${msg.message()}",
                    )
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

    override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
        Log.d(TAG, "Page started: $url")
    }

    override fun onPageFinished(view: WebView, url: String) {
        Log.d(TAG, "Page finished: $url")
    }

    override fun onReceivedError(
        view: WebView,
        request: WebResourceRequest,
        error: WebResourceError,
    ) {
        // The signal we were missing in real-world testing: when a captive
        // portal page fails to load (DNS, EPERM via the sandboxed WebView
        // process, TLS cert error, redirect loop, etc.) the WebView quietly
        // shows a blank page. Logging the error code + description here makes
        // the failure visible in logcat.
        Log.w(
            TAG,
            "onReceivedError ${request.url}: code=${error.errorCode} desc=${error.description} " +
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
            "onReceivedHttpError ${request.url}: status=${errorResponse.statusCode} " +
                "reason=${errorResponse.reasonPhrase} isMainFrame=${request.isForMainFrame}",
        )
    }

    override fun shouldOverrideUrlLoading(
        view: WebView,
        request: WebResourceRequest,
    ): Boolean {
        val requestHost = runCatching { request.url.host ?: "" }.getOrDefault("")
        val isSameOrigin = requestHost == portalHost || requestHost.endsWith(".$portalHost")
        return if (isSameOrigin) {
            false // allow WebView to load it
        } else {
            Log.d(TAG, "Blocking off-domain navigation to ${request.url} (portal host=$portalHost)")
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
