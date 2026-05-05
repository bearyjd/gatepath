package cc.grepon.gatepath.ui

import android.net.ConnectivityManager
import android.net.Network
import android.webkit.CookieManager
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

    override fun shouldOverrideUrlLoading(
        view: WebView,
        request: WebResourceRequest,
    ): Boolean {
        val requestHost = runCatching { request.url.host ?: "" }.getOrDefault("")
        val isSameOrigin = requestHost == portalHost || requestHost.endsWith(".$portalHost")
        return if (isSameOrigin) {
            false // allow WebView to load it
        } else {
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
