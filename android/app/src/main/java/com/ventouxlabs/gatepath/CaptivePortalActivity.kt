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
import com.ventouxlabs.gatepath.network.ProcessBinding
import com.ventouxlabs.gatepath.network.VpnDetector
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.network.classify
import com.ventouxlabs.gatepath.ui.ConfinementAction
import com.ventouxlabs.gatepath.ui.ConfinementCard
import com.ventouxlabs.gatepath.ui.ConfinementStateText
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
 *     activity acquires a [ProcessBinding.Lease] on this network so the
 *     WebView's traffic routes via the captive interface — see
 *     [ProcessBinding] for the owner/borrower model this participates in.
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
    lateinit var processBinding: ProcessBinding

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
     * The lease this activity acquired in [onCreate], so [onDestroy] can
     * release exactly that lease — see [ProcessBinding] for the owner-stack
     * model this participates in. Null until acquired (or if acquisition was
     * refused).
     */
    private var lease: ProcessBinding.Lease? = null

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
            ?: processBinding.current()
            ?: connectivityManager.activeNetwork

        if (network == null) {
            Log.w(TAG, "No Network found for captive portal; finishing")
            portal.ignoreNetwork()
            finish()
            return
        }

        // Acquire a lease on the captive network so the WebView's traffic
        // routes via that interface. A refused acquire (e.g. EPERM under a
        // secure VPN — the Tunnelled/Blocked path) returns null and claims
        // nothing; classification below still runs and explains why.
        lease = processBinding.acquire(network)
        if (lease == null) {
            Log.w(TAG, "processBinding.acquire($network) refused; not claiming the binding")
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
            val vpnKind = VpnKind.fromInterfaces(vpn.interfaces)
            render(state, if (state is ConfinementState.Confined) handoffUrl else null, network, handoffUrl, vpnKind)
        }
    }

    /**
     * Show the sign-in page when [url] is non-null, otherwise the one action
     * this confinement state allows. Sharing is absent on purpose: the
     * evidence bundle belongs to the ViewModel in `MainActivity`, and this
     * entry point has no session of its own to attach it to.
     *
     * **Unknown is rendered with this entry point's own copy**
     * ([ConfinementStateText.handoffUnknown]), because the default Unknown
     * action is "share evidence" and there is nothing here to share:
     *
     * - With no VPN interface up, the card offers to open the WebView at
     *   [handoffUrl] anyway. Reaching this activity at all means the system
     *   handed Gatepath a `CaptivePortal` token because *its* probe saw a
     *   portal, which outranks our own inconclusive one — so the honest
     *   offer is "try anyway", not a dead end.
     * - With a VPN interface up ([vpnKind] is not `NONE`), the card offers
     *   "Open VPN app" instead, exactly as [ConfinementState.Tunnelled] does.
     *   `Unknown` means the probe's failure had no typed errno
     *   ([com.ventouxlabs.gatepath.network.probeErrorReason]), and under a
     *   VPN an inconclusive probe is far more likely a tunnelled bind the
     *   platform did not surface than a real portal. Offering "try signing
     *   in anyway" there would hand a tunnelled user the one action this
     *   feature exists to withhold; offering nothing would leave a button
     *   that does nothing. A bound probe that actually returned 204 is told
     *   apart from a genuine probe error by
     *   [com.ventouxlabs.gatepath.network.UnknownReason.BOUND_VALIDATED] on
     *   the state, but that alone only proves the probe's own socket routed
     *   correctly — not that [lease] (the process-wide bind the WebView
     *   actually depends on) was granted. The sign-in offer under a VPN is
     *   kept only when [lease] is also non-null; see
     *   [ConfinementStateText.handoffUnknown].
     *
     * [ConfinementState.Tunnelled], [ConfinementState.Blocked] and
     * [ConfinementState.DnsStrict] keep their existing actions and never open
     * the WebView: for those we know *why* the page cannot load, and opening
     * it would reproduce the blank screen this flow exists to prevent.
     */
    private fun render(state: ConfinementState, url: String?, network: Network, handoffUrl: String, vpnKind: VpnKind) {
        setContent {
            GatepathTheme {
                if (url != null) {
                    PortalScreen(
                        portalUrl = url,
                        network = network,
                        processBinding = processBinding,
                        onDismiss = ::reportSignedIn,
                        onBlockedNavigation = {},
                        onBlockedResource = {},
                        onTlsCertErrorBypassed = {},
                        // No evidence record on this entry point — see render's KDoc.
                        onCertSummary = {},
                    )
                } else {
                    val kind = when (state) {
                        is ConfinementState.Tunnelled -> state.vpnKind
                        is ConfinementState.Blocked -> state.vpnKind
                        is ConfinementState.Unknown -> vpnKind
                        is ConfinementState.Confined, is ConfinementState.DnsStrict -> VpnKind.NONE
                    }
                    val (label, launch) = VpnAppLauncher.resolve(this, kind)
                    // Unknown gets this entry point's own sentence and action;
                    // see this function's KDoc. Null for every other state.
                    val handoffUnknown = (state as? ConfinementState.Unknown)
                        ?.let { ConfinementStateText.handoffUnknown(it.reason, vpnKind, label, processBindHeld = lease != null) }
                    // Scaffold, not a bare card: enableEdgeToEdge() is active,
                    // so without innerPadding the card draws under the status
                    // and navigation bars.
                    Scaffold { innerPadding ->
                        ConfinementCard(
                            state = state,
                            vpnAppLabel = label,
                            onAction = { action ->
                                // The card dispatches the state's default
                                // action (SHARE_EVIDENCE for Unknown); on
                                // this entry point that means the handoff
                                // copy's action instead.
                                val effective = handoffUnknown?.action ?: action
                                when (effective) {
                                    ConfinementAction.OPEN_VPN_APP -> startActivity(launch)
                                    ConfinementAction.OPEN_NETWORK_SETTINGS ->
                                        startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
                                    ConfinementAction.SIGN_IN_HERE ->
                                        render(state, handoffUrl, network, handoffUrl, vpnKind)
                                    // Unreachable here: Unknown is the only
                                    // state whose default action is sharing,
                                    // and it is remapped above.
                                    ConfinementAction.SHARE_EVIDENCE -> Unit
                                }
                            },
                            onShareEvidence = {},
                            modifier = Modifier.padding(innerPadding),
                            actionLabelOverride = handoffUnknown?.actionLabel,
                            sentenceOverride = handoffUnknown?.sentence,
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
        // Release this activity's own lease, if it holds one. This is safe
        // under MainActivity's live WebView (which holds its own lease on the
        // owner stack) precisely because ProcessBinding tracks owners by
        // lease, not by comparing the process-wide slot's current value —
        // see ProcessBinding's KDoc for the full owner/borrower model this
        // replaced the old per-caller compare-and-null with.
        lease?.let(processBinding::release)
        lease = null
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
