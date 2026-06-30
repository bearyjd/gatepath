package cc.grepon.gatepath.testvpn

import android.app.Activity
import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import android.util.Log
import cc.grepon.gatepath.BuildConfig
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/** DEBUG-ONLY harness control surface, driven by `am start … --es gatepath.testvpn.action <a>`. */
class TestVpnControlActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (BuildConfig.DEBUG) handle(intent)
        finish()
    }

    private fun handle(intent: Intent) {
        when (intent.getStringExtra(EXTRA_ACTION)) {
            "start" -> {
                if (VpnService.prepare(this) != null) { Log.e(TAG, "VPN not authorized"); return }
                startService(svc(GatepathTestVpnService.ACTION_START))
            }
            "probe" -> sendUnboundProbe()
            "stop" -> startService(svc(GatepathTestVpnService.ACTION_STOP))
            else -> Log.w(TAG, "unknown action")
        }
    }

    private fun svc(action: String) =
        Intent(this, GatepathTestVpnService::class.java).setAction(action)

    private fun sendUnboundProbe() {
        // Off the main thread: DatagramSocket.send is network I/O and would throw
        // NetworkOnMainThreadException in onCreate. join() so the activity doesn't
        // finish before the datagrams are flushed to the (VPN) default route.
        val addr = InetAddress.getByName(SENTINEL_IP)
        Thread {
            DatagramSocket().use { sock ->
                repeat(PROBE_COUNT) {
                    val p = "gatepath-liveness".toByteArray()
                    sock.send(DatagramPacket(p, p.size, addr, SENTINEL_PORT))
                }
            }
        }.apply { start(); join() }
        Log.i(TAG, "sent $PROBE_COUNT datagrams to $SENTINEL_IP:$SENTINEL_PORT")
    }

    companion object {
        private const val TAG = "GatepathTestVpnCtl"
        const val EXTRA_ACTION = "gatepath.testvpn.action"
        const val SENTINEL_IP = "203.0.113.7"
        const val SENTINEL_PORT = 9
        const val PROBE_COUNT = 3
    }
}
