package com.ventouxlabs.gatepath

import android.content.Intent
import android.net.CaptivePortal
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import com.ventouxlabs.gatepath.network.CONNECTIVITY_CHECK_URL
import com.ventouxlabs.gatepath.network.CaptivePortalMonitor
import com.ventouxlabs.gatepath.network.ClassificationInputs
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.PortalProbe
import com.ventouxlabs.gatepath.network.ProbeResult
import com.ventouxlabs.gatepath.network.VpnDetector
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.network.classify
import com.ventouxlabs.gatepath.ui.ConfinementAction
import com.ventouxlabs.gatepath.ui.ConfinementCard
import com.ventouxlabs.gatepath.ui.PortalScreen
import com.ventouxlabs.gatepath.ui.VpnAppLauncher
import com.ventouxlabs.gatepath.ui.theme.GatepathTheme
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.URI
import javax.inject.Inject

/**
 * System captive-portal handler.
 *
 * Launched by Android when the user taps the "Sign in to Wi-Fi network"
 * notification AND picks Gatepath in the chooser. The intent carries:
 *
 *   - [ConnectivityManager.EXTRA_CAPTIVE_PORTAL] — a [CaptivePortal] token
 *     used to report sign-in completion or dismissal back to the system.
 *
 *   - [ConnectivityManager.EXTRA_NETWORK] — the captive [Network]. The
 *     activity binds the process to this network via
 *     [ConnectivityManager.bindProcessToNetwork] so the WebView's traffic
 *     routes via the captive interface.
 *
 *   - [ConnectivityManager.EXTRA_CAPTIVE_PORTAL_URL] — the URL the captive
 *     portal redirected to (the actual sign-in page). Available API 28+.
 *
 * Arriving here is not proof that the traffic can reach the gateway: a
 * secure VPN still covers this UID, so the bind can still fail with EPERM.
 * The activity therefore classifies the network first ([ConfinementState])
 * and shows a WebView only from [ConfinementState.Confined]; every other
 * state gets the confinement card instead of a page that cannot load.
 *
 * On dismiss with success → [CaptivePortal.reportCaptivePortalDismissed].
 * On dismiss without success (back button, system kill) → [CaptivePortal.ignoreNetwork].
 */
@AndroidEntryPoint
class CaptivePortalActivity : ComponentActivity() {

    @Inject
    lateinit var connectivityManager: ConnectivityManager

    @Inject
    lateinit var probe: PortalProbe

    /**
     * Injected only for [CaptivePortalMonitor.probeUrl]. This entry point must
     * probe the same endpoint the monitor does — in debug builds `AppModule`
     * resolves that from `Settings.Global.captive_portal_http_url`, so a
     * hardcoded gstatic URL here would answer 204 over the emulator's real NAT
     * and classify a genuinely captive network as Unknown. See
     * `tests/e2e-android/HARNESS_NOTES.md` §2.
     */
    @Inject
    lateinit var monitor: CaptivePortalMonitor

    private var captivePortal: CaptivePortal? = null
    private var reported = false

    /**
     * The network this activity bound the process to in [onCreate], so
     * [onDestroy] releases only a binding it still owns. Null until bound.
     */
    private var boundNetwork: Network? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        captivePortal = readCaptivePortalExtra(intent)
        // Nullable on purpose. Substituting the constant here would make the
        // system's absent URL indistinguishable from a real one, and the
        // probe's own discovered location — a better answer — would never be
        // consulted. Resolution happens after classification, below.
        val intentPortalUrl = intent.getStringExtra(ConnectivityManager.EXTRA_CAPTIVE_PORTAL_URL)

        val portal = captivePortal
        if (portal == null) {
            Log.w(TAG, "CAPTIVE_PORTAL intent missing CaptivePortal extra; finishing")
            finish()
            return
        }

        // The captive portal Network is delivered via EXTRA_NETWORK on newer
        // API levels. Fall back to the currently-bound or active network if
        // the extra is missing.
        val network: Network? = readNetworkExtra(intent)
            ?: connectivityManager.boundNetworkForProcess
            ?: connectivityManager.activeNetwork

        if (network == null) {
            Log.w(TAG, "No Network found for captive portal; finishing")
            portal.ignoreNetwork()
            finish()
            return
        }

        // Bind the process to the captive network so the WebView's traffic
        // routes via that interface. Ownership is claimed only when the bind
        // actually took: under a secure VPN it is refused (EPERM — the
        // Tunnelled/Blocked path), and claiming a slot we never set would let
        // onDestroy clear a binding another screen legitimately owns.
        if (connectivityManager.bindProcessToNetwork(network)) {
            boundNetwork = network
        } else {
            Log.w(TAG, "bindProcessToNetwork($network) refused; not claiming the binding")
        }

        Log.i(
            TAG,
            "Handling captive portal for network $network at ${intentPortalUrl ?: "(no url in intent)"}",
        )

        // Classification below can take tens of seconds against a gateway that
        // black-holes the probe (5s connect + 5s read, plus the VPN detector's
        // 2s+2s Tailscale localapi call). Put something on screen first: the
        // system just handed this activity the foreground, and a blank window
        // reads as a crash.
        setContent { GatepathTheme { ConfinementProbePlaceholder() } }

        lifecycleScope.launch {
            val bound = withContext(Dispatchers.IO) { probe.probe(network, testUrl = monitor.probeUrl) }
            val vpn = withContext(Dispatchers.IO) { VpnDetector.detect() }
            val strict = connectivityManager.getLinkProperties(network)?.privateDnsServerName != null
            val resolved = (bound as? ProbeResult.Portal)?.let { p ->
                val host = runCatching { URI(p.locationUrl).host }.getOrNull()
                if (host == null || host.all { it.isDigit() || it == '.' } || host.contains(':')) null
                else withContext(Dispatchers.IO) { runCatching { network.getAllByName(host).isNotEmpty() }.getOrDefault(false) }
            }
            val state = classify(ClassificationInputs(bound, null, vpn.interfaces, strict, resolved))
            Log.i(TAG, "System handoff confinement: ${state.schemaName}")
            // Best available sign-in URL, in descending order of authority:
            // the system's extra, then the location the probe actually found,
            // then the check URL itself — which the gateway is intercepting
            // anyway, so loading it yields the same login page.
            val handoffUrl = intentPortalUrl
                ?: (state as? ConfinementState.Confined)?.portalUrl
                ?: CONNECTIVITY_CHECK_URL
            val vpnPresent = VpnKind.fromInterfaces(vpn.interfaces) != VpnKind.NONE
            render(state, if (state is ConfinementState.Confined) handoffUrl else null, network, handoffUrl, vpnPresent)
        }
    }

    /**
     * Show the sign-in page when [url] is non-null, otherwise the one action
     * this confinement state allows. Sharing is absent on purpose: the
     * evidence bundle belongs to the ViewModel in `MainActivity`, and this
     * entry point has no session of its own to attach it to.
     *
     * **Unknown carve-out, this entry point only, and only without a VPN.**
     * From [ConfinementState.Unknown] the card's action opens the WebView at
     * [handoffUrl] rather than doing nothing. Reaching this activity at all
     * means the system handed Gatepath a `CaptivePortal` token because *its*
     * probe saw a portal, which is a stronger signal than our own
     * inconclusive one — so the honest offer is "try anyway", not a dead end.
     * [ConfinementState.Tunnelled], [ConfinementState.Blocked] and
     * [ConfinementState.DnsStrict] keep their existing actions and never open
     * the WebView: for those we know *why* the page cannot load, and opening
     * it would reproduce the blank screen this flow exists to prevent.
     *
     * The carve-out is withheld when [vpnPresent] is true. `Unknown` means
     * [probeErrorReason] could not pin the bound probe's failure to a typed
     * errno — but under a VPN, an inconclusive probe is far more likely a
     * tunnelled bind netd refused for a reason this device's platform didn't
     * surface as a typed [com.ventouxlabs.gatepath.network.ProbeErrorReason]
     * than a real captive portal. Offering "try signing in anyway" there
     * would hand a tunnelled user exactly the action this feature exists to
     * withhold. With no VPN present, `Unknown` keeps the carve-out.
     */
    private fun render(state: ConfinementState, url: String?, network: Network, handoffUrl: String, vpnPresent: Boolean) {
        setContent {
            GatepathTheme {
                if (url != null) {
                    PortalScreen(
                        portalUrl = url,
                        network = network,
                        connectivityManager = connectivityManager,
                        onDismiss = ::reportSignedIn,
                        onBlockedNavigation = {},
                        onBlockedResource = {},
                        onTlsCertErrorBypassed = {},
                        // No evidence record on this entry point — see render's KDoc.
                        onCertSummary = {},
                    )
                } else {
                    val kind = (state as? ConfinementState.Tunnelled)?.vpnKind
                        ?: (state as? ConfinementState.Blocked)?.vpnKind ?: VpnKind.NONE
                    val (label, launch) = VpnAppLauncher.resolve(this, kind)
                    // Withheld under a VPN — see the carve-out in this
                    // function's KDoc.
                    val offerUnknownCarveOut = state is ConfinementState.Unknown && !vpnPresent
                    // Scaffold, not a bare card: enableEdgeToEdge() is active,
                    // so without innerPadding the card draws under the status
                    // and navigation bars.
                    Scaffold { innerPadding ->
                        ConfinementCard(
                            state = state,
                            vpnAppLabel = label,
                            onAction = { action ->
                                when (action) {
                                    ConfinementAction.OPEN_VPN_APP -> startActivity(launch)
                                    ConfinementAction.OPEN_NETWORK_SETTINGS ->
                                        startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
                                    // Unknown's action, repurposed here — see
                                    // the carve-out in this function's KDoc.
                                    ConfinementAction.SHARE_EVIDENCE ->
                                        if (offerUnknownCarveOut) {
                                            render(state, handoffUrl, network, handoffUrl, vpnPresent)
                                        } else {
                                            Unit
                                        }
                                    ConfinementAction.SIGN_IN_HERE -> Unit
                                }
                            },
                            onShareEvidence = {},
                            modifier = Modifier.padding(innerPadding),
                            actionLabelOverride = if (offerUnknownCarveOut) "Try signing in anyway" else null,
                            // No bundle on this entry point — MainActivity owns
                            // sharing. Rendering the button here would show a
                            // control that silently does nothing.
                            showShareEvidence = false,
                        )
                    }
                }
            }
        }
    }

    /**
     * What the user looks at while the network is being classified. Deliberately
     * says nothing about the outcome — at this point Gatepath does not yet know
     * whether it can reach the gateway.
     */
    @Composable
    private fun ConfinementProbePlaceholder() {
        Scaffold { innerPadding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding)
                    .padding(24.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                CircularProgressIndicator()
                Text(
                    text = "Checking this network…",
                    style = MaterialTheme.typography.bodyLarge,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(top = 16.dp),
                )
            }
        }
    }

    /**
     * Tell the system the user signed in. The system re-validates the network
     * and clears the captive-portal flag. Subsequent connectivity goes
     * through the normal validated path.
     */
    private fun reportSignedIn() {
        if (reported) {
            finish()
            return
        }
        reported = true
        captivePortal?.reportCaptivePortalDismissed()
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        // Activity destroyed without reporting (back button, low-memory kill).
        // Tell the system we ignored the network so it falls back to its own
        // handler instead of waiting indefinitely for our reply.
        if (!reported) {
            captivePortal?.ignoreNetwork()
        }
        // Undo onCreate's process-global bind. The bind is set on the process,
        // which this activity shares with MainViewModel, so it outlives the
        // activity. On the WebView path PortalScreen's own DisposableEffect
        // clears it; three paths here never compose a WebView at all (the
        // classification placeholder, the Tunnelled/Blocked/DnsStrict card,
        // and an Unknown card the user never taps). GatepathApplication's
        // BindWatchdog is a backstop, but it only fires when the whole app
        // backgrounds — backing out of a handoff card into MainActivity keeps
        // the app foregrounded, so without this the process stays bound to the
        // captive Wi-Fi and the diagnostic engine's deliberately-unbound probes
        // travel it instead of the default route.
        //
        // Conditional, not unconditional: the binding is one slot shared by
        // every screen in the process. MainActivity's PortalScreen may have
        // bound the process to a *different* network while this card was up
        // (the monitor opens sessions on its own now, and the system handoff
        // arrives independently). Nulling that binding here would send the
        // live portal WebView's traffic over the default route — the leak the
        // no-leak sentinel exists to disprove. So release only what this
        // activity set: if the slot no longer holds our network, someone else
        // owns it and it is theirs to clear (PortalScreen's own onDispose does).
        //
        // Residuals, tracked for the follow-up (both need a refcounted binding
        // owner rather than a per-caller compare):
        //  (a) if another screen bound the *same* network, this check cannot
        //      tell the two owners apart;
        //  (b) CaptivePortalMonitor.probeAndEmit is a borrower, not an owner:
        //      it saves the slot, binds its probe network, and writes the saved
        //      value back in a `finally` on Dispatchers.IO. If a probe is in
        //      flight when this runs, that write-back restores our network
        //      after we cleared it, and the process ends up bound with nobody
        //      left to release it. Pre-existing — the old unconditional
        //      null-bind lost the same race — and BindWatchdog remains the
        //      only backstop.
        val ours = boundNetwork
        if (ours != null && connectivityManager.boundNetworkForProcess == ours) {
            connectivityManager.bindProcessToNetwork(null)
        }
        boundNetwork = null
    }

    @Suppress("DEPRECATION")
    private fun readCaptivePortalExtra(intent: Intent): CaptivePortal? {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(
                ConnectivityManager.EXTRA_CAPTIVE_PORTAL,
                CaptivePortal::class.java,
            )
        } else {
            intent.getParcelableExtra(ConnectivityManager.EXTRA_CAPTIVE_PORTAL)
        }
    }

    @Suppress("DEPRECATION")
    private fun readNetworkExtra(intent: Intent): Network? {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(
                ConnectivityManager.EXTRA_NETWORK,
                Network::class.java,
            )
        } else {
            intent.getParcelableExtra(ConnectivityManager.EXTRA_NETWORK)
        }
    }

    companion object {
        private const val TAG = "GatepathCaptive"
    }
}
