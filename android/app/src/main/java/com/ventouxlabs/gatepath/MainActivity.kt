package com.ventouxlabs.gatepath

import android.content.Intent
import android.net.ConnectivityManager
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.lifecycleScope
import com.ventouxlabs.gatepath.diag.IncidentEvidence
import com.ventouxlabs.gatepath.network.ConfinementState
import com.ventouxlabs.gatepath.network.VpnKind
import com.ventouxlabs.gatepath.session.PortalSession
import com.ventouxlabs.gatepath.share.DiagnosticsSharer
import com.ventouxlabs.gatepath.ui.ConfinementAction
import com.ventouxlabs.gatepath.ui.MainScreen
import com.ventouxlabs.gatepath.ui.PortalScreen
import com.ventouxlabs.gatepath.ui.VpnAppLauncher
import com.ventouxlabs.gatepath.ui.theme.GatepathTheme
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import java.io.File
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject
    lateinit var connectivityManager: ConnectivityManager

    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        maybeApplyDebugIntent(intent)

        // The monitor runs from the ViewModel's init, so a classification can
        // land before this Activity exists. The setter replays the current
        // state, so the harness artefact is written for that case too.
        if (BuildConfig.DEBUG) {
            // applicationContext, not the Activity: the sink is held by the
            // ViewModel, which outlives this Activity across a rotation, and
            // capturing `filesDir` here would capture `this` with it.
            val filesDir = applicationContext.filesDir
            viewModel.debugStateSink = { name ->
                File(filesDir, DEBUG_STATE_FILE).writeText(name)
            }
        }

        setContent {
            GatepathTheme {
                val session by viewModel.session.collectAsState()
                val activeNetwork by viewModel.activeNetwork.collectAsState()
                val networkStatus by viewModel.networkStatus.collectAsState()
                val diagnosis by viewModel.diagnosis.collectAsState()
                val evidence by viewModel.evidence.collectAsState()
                val confinement by viewModel.confinement.collectAsState()

                // Only Tunnelled and Blocked name a VPN; every other state
                // sends the user somewhere that isn't a VPN app, and NONE
                // makes VpnAppLauncher fall back to the system VPN settings.
                val context = LocalContext.current
                val vpnKind = (confinement as? ConfinementState.Tunnelled)?.vpnKind
                    ?: (confinement as? ConfinementState.Blocked)?.vpnKind
                    ?: VpnKind.NONE
                // Package enumeration hits the PackageManager, so resolve once
                // per classification rather than on every recomposition.
                val vpnAppLabel = remember(confinement) {
                    VpnAppLauncher.resolve(context, vpnKind).first
                }

                when (val s = session) {
                    is PortalSession.Active -> {
                        val network = activeNetwork
                        if (network != null) {
                            PortalScreen(
                                portalUrl = s.portalUrl,
                                network = network,
                                connectivityManager = connectivityManager,
                                onDismiss = viewModel::onDismiss,
                                onBlockedNavigation = viewModel::onBlockedNavigation,
                                onBlockedResource = viewModel::onBlockedResource,
                                onTlsCertErrorBypassed = viewModel::onTlsCertErrorBypassed,
                                onCertSummary = viewModel::onCertSummary,
                            )
                        } else {
                            MainScreen(
                                session = s,
                                networkStatus = networkStatus,
                                confinement = confinement,
                                vpnAppLabel = vpnAppLabel,
                                diagnosis = diagnosis,
                                onDismiss = viewModel::onDismiss,
                                onAction = { action -> onConfinementAction(action, vpnKind) },
                                onRunDiagnostics = viewModel::rerunDiagnostics,
                                onShareDiagnostics = { redact -> shareDiagnostics(redact, evidence) },
                            )
                        }
                    }
                    else -> MainScreen(
                        session = s,
                        networkStatus = networkStatus,
                        confinement = confinement,
                        vpnAppLabel = vpnAppLabel,
                        diagnosis = diagnosis,
                        onDismiss = viewModel::onDismiss,
                        onAction = { action -> onConfinementAction(action, vpnKind) },
                        onRunDiagnostics = viewModel::rerunDiagnostics,
                        onShareDiagnostics = { redact -> shareDiagnostics(redact, evidence) },
                    )
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        maybeApplyDebugIntent(intent)
    }

    /**
     * The one action the confinement card offers. SHARE_EVIDENCE is absent on
     * purpose: [MainScreen] owns the redaction dialog, so it handles that case
     * itself and never routes it here.
     */
    private fun onConfinementAction(action: ConfinementAction, kind: VpnKind) {
        when (action) {
            ConfinementAction.SIGN_IN_HERE -> viewModel.signInHere()
            ConfinementAction.OPEN_VPN_APP -> startActivity(VpnAppLauncher.resolve(this, kind).second)
            ConfinementAction.OPEN_NETWORK_SETTINGS -> startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
            ConfinementAction.SHARE_EVIDENCE -> Unit // handled by MainScreen's dialog
        }
    }

    /**
     * Assemble the diagnostics bundle (audit log + latest diagnosis) and hand it
     * to the system share sheet. [redact] scrubs the network-identifying fields;
     * see [DiagnosticsSharer] / [com.ventouxlabs.gatepath.diag.DiagnosticsBundle].
     *
     * File I/O runs off the main thread inside [DiagnosticsSharer.writeBundle];
     * the chooser is launched on the resulting URI.
     */
    private fun shareDiagnostics(redact: Boolean, evidence: IncidentEvidence?) {
        lifecycleScope.launch {
            try {
                val uri = DiagnosticsSharer.writeBundle(
                    context = this@MainActivity,
                    diagnosis = viewModel.diagnosis.value,
                    evidence = evidence,
                    redact = redact,
                )
                val sendIntent =
                    DiagnosticsSharer.sendIntent(uri, getString(R.string.share_diagnostics_subject))
                startActivity(
                    Intent.createChooser(sendIntent, getString(R.string.share_diagnostics_chooser)),
                )
            } catch (e: CancellationException) {
                throw e // cooperative cancellation is not a failure — never swallow it
            } catch (e: Exception) {
                Log.e(TAG, "Share diagnostics failed", e)
                Toast.makeText(this@MainActivity, R.string.share_diagnostics_error, Toast.LENGTH_LONG)
                    .show()
            }
        }
    }

    /**
     * Debug-only entry point: open PortalScreen against a user-supplied URL
     * without going through the captive-portal detection pipeline. Exists for
     * smoke-testing the WebView/PortalScreen code path on devices whose system
     * captive detection is unreachable (e.g. GrapheneOS hardcodes the probe
     * URLs in its NetworkStack module, ignoring Settings.Global overrides).
     *
     * Fire from adb:
     *   adb shell am start -n com.ventouxlabs.gatepath/.MainActivity \
     *       --es gatepath.debug.portal_url "http://your-portal/portal"
     */
    private fun maybeApplyDebugIntent(intent: Intent) {
        if (!BuildConfig.DEBUG) return
        // Breadcrumb: proves the Intent was actually delivered. Its absence is
        // how the e2e harness learned `am start` was resuming the task without
        // calling onNewIntent — see run-scenario.py's --activity-single-top.
        Log.i(TAG, "Debug intent received: extras=${intent.extras?.keySet()}")
        if (intent.getBooleanExtra(EXTRA_DEBUG_WRITE_BUNDLE, false)) {
            debugWriteDiagnosticsBundle(intent.getBooleanExtra(EXTRA_DEBUG_REDACT, true))
            return
        }
        val url = intent.getStringExtra(EXTRA_DEBUG_PORTAL_URL) ?: return
        val net = connectivityManager.activeNetwork ?: run {
            Log.w(TAG, "Debug portal intent: no active network; ignored")
            return
        }
        Log.i(TAG, "Debug portal intent: opening $url on $net")
        viewModel.debugForceActiveSession(url, net)
    }

    /**
     * Debug-only: build the diagnostics bundle and log where it landed, without
     * launching the chooser.
     *
     * The e2e harness cannot drive the system share sheet for the same reason it
     * cannot drive the captive-portal notification — see `HARNESS_NOTES.md §1`.
     * Everything worth testing on this path happens before the chooser anyway:
     * the bundle is assembled, written to the FileProvider-shareable cache dir,
     * and a `content://` URI is minted, which is where an authority or
     * `file_paths.xml` mistake would surface. `sendIntent` after it is a
     * four-line `Intent` builder.
     *
     * Fire from adb (debug builds only; release-stripped):
     *   adb shell am start -n com.ventouxlabs.gatepath/.MainActivity \
     *       --ez gatepath.debug.write_bundle true --ez gatepath.debug.redact true
     */
    private fun debugWriteDiagnosticsBundle(redact: Boolean) {
        lifecycleScope.launch {
            try {
                val uri = DiagnosticsSharer.writeBundle(
                    context = this@MainActivity,
                    diagnosis = viewModel.diagnosis.value,
                    evidence = viewModel.evidence.value,
                    redact = redact,
                )
                // Signal completion through a FILE, not logcat. The harness
                // cannot depend on logcat here (HARNESS_NOTES §3: boot spam
                // buries app lines and the ring buffer rotates them out).
                // Written only AFTER getUriForFile returns, so its existence
                // proves the FileProvider authority resolved — which writing
                // the bundle alone does not, since writeText happens first.
                File(filesDir, DEBUG_BUNDLE_URI_FILE).writeText(uri.toString())
                Log.i(TAG, "$DEBUG_BUNDLE_MARKER redact=$redact uri=$uri")
            } catch (e: CancellationException) {
                throw e // cooperative cancellation is not a failure
            } catch (e: Exception) {
                Log.e(TAG, "$DEBUG_BUNDLE_MARKER failed", e)
            }
        }
    }

    companion object {
        private const val TAG = "GatepathMain"
        private const val EXTRA_DEBUG_PORTAL_URL = "gatepath.debug.portal_url"
        private const val EXTRA_DEBUG_WRITE_BUNDLE = "gatepath.debug.write_bundle"
        private const val EXTRA_DEBUG_REDACT = "gatepath.debug.redact"

        /** Also logged, but only as a human breadcrumb — the harness reads the file below. */
        private const val DEBUG_BUNDLE_MARKER = "debug_bundle_written"

        /** The e2e harness polls for this via run-as; keep in sync with run-scenario.py. */
        private const val DEBUG_BUNDLE_URI_FILE = "debug-bundle-uri.txt"

        /**
         * Debug-only artefact holding the latest `ConfinementState.schemaName`.
         * The harness reads it via run-as; keep in sync with run-scenario.py.
         */
        private const val DEBUG_STATE_FILE = "confinement-state.txt"
    }
}
