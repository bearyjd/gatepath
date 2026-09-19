package com.ventouxlabs.gatepath.testvpn

import android.app.Activity
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.net.VpnService
import android.os.Bundle
import android.util.Log
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Control surface for the standalone `:testvpn` app, driven by
 * `am start … --es gatepath.testvpn.action <a>` from the e2e harness.
 *
 * This activity can start a VPN that logs the destination of every packet
 * Gatepath emits, so it is gated twice rather than by policy alone:
 *
 * - **Guarded by `android.permission.DUMP`.** The shell uid holds it (so
 *   `adb shell am start` works) and no third-party app can; it is not
 *   simply non-exported because the shell does not hold `START_ANY_ACTIVITY`
 *   and a non-exported target is refused for it.
 * - **Debuggable builds only.** `handle` runs only when the installed
 *   package is debuggable — the same `BuildConfig.DEBUG`-style gate the
 *   in-app debug intents use, checked at runtime so it does not depend on
 *   a generated BuildConfig. The release variant is disabled in
 *   `build.gradle.kts` as well, so a non-debuggable build cannot exist.
 */
class TestVpnControlActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (isDebuggable()) {
            handle(intent)
        } else {
            Log.e(TAG, "refusing control intent: package is not debuggable")
        }
        finish()
    }

    private fun isDebuggable(): Boolean =
        (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0

    private fun handle(intent: Intent) {
        when (intent.getStringExtra(EXTRA_ACTION)) {
            "start" -> {
                if (VpnService.prepare(this) != null) { Log.e(TAG, "VPN not authorized"); return }
                val mode = intent.getStringExtra(GatepathTestVpnService.EXTRA_MODE) ?: "covering"
                startService(
                    svc(GatepathTestVpnService.ACTION_START)
                        .putExtra(GatepathTestVpnService.EXTRA_MODE, mode),
                )
            }
            "probe" -> sendUnboundProbe()
            "mark" -> {
                // Write the marker line DIRECTLY (in-process) via the shared sink
                // helper. Routing it through startService would throw
                // BackgroundServiceStartNotAllowedException on Android 14 — this
                // NoDisplay activity finishes instantly, so the startService call
                // counts as a background start and the mark would be dropped.
                val label = intent.getStringExtra(EXTRA_LABEL) ?: "?"
                GatepathTestVpnService.appendLine(
                    filesDir,
                    org.json.JSONObject().put("marker", label)
                        .put("t", System.currentTimeMillis() / 1000.0).toString(),
                )
            }
            "stop" -> startService(svc(GatepathTestVpnService.ACTION_STOP))
            else -> Log.w(TAG, "unknown action")
        }
    }

    private fun svc(action: String) =
        Intent(this, GatepathTestVpnService::class.java).setAction(action)

    private fun sendUnboundProbe() {
        // Off the main thread: Socket.connect is network I/O and would throw
        // NetworkOnMainThreadException in onCreate. join() so the activity doesn't
        // finish before the SYN(s) are flushed to the (VPN) default route.
        //
        // An UNBOUND TCP connect to the routable sentinel host:port. Nothing
        // listens there, so the connect fails (refused / timed out / black-holed)
        // — that is expected; the outbound SYN is the signal the VPN sink must
        // capture. TCP-to-routable reaches the TUN reliably on the emulator where
        // unroutable UDP did not (PR #55, issue #2). A few attempts for robustness.
        Thread {
            repeat(PROBE_COUNT) {
                try {
                    Socket().use { sock ->
                        sock.connect(
                            InetSocketAddress(SENTINEL_HOST, SENTINEL_PORT),
                            CONNECT_TIMEOUT_MS,
                        )
                    }
                } catch (_: Exception) {
                    // Expected: nothing listens at the sentinel — the SYN is the signal.
                }
            }
        }.apply { start(); join() }
        Log.i(TAG, "sent sentinel probe to $SENTINEL_HOST:$SENTINEL_PORT")
    }

    companion object {
        private const val TAG = "GatepathTestVpnCtl"
        const val EXTRA_ACTION = "gatepath.testvpn.action"
        const val EXTRA_LABEL = "gatepath.testvpn.label"
        const val SENTINEL_HOST = "10.0.2.2"
        const val SENTINEL_PORT = 18081
        private const val PROBE_COUNT = 3
        private const val CONNECT_TIMEOUT_MS = 1500
    }
}
