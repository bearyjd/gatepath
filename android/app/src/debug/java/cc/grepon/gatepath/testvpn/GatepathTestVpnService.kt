package cc.grepon.gatepath.testvpn

import android.content.Intent
import android.net.VpnService
import android.os.ParcelFileDescriptor
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream

/**
 * DEBUG-ONLY local VpnService used by the android-e2e no-leak sentinel
 * (ROADMAP P0.1). Becomes the system default network and records the
 * destination of every IPv4 packet the Gatepath app emits while unbound,
 * to files/vpn-sink.jsonl. Never forwards (a black hole). Absent from
 * release builds — lives in src/debug/.
 */
class GatepathTestVpnService : VpnService() {

    @Volatile private var running = false
    private var tun: ParcelFileDescriptor? = null
    private val sinkLock = Any()

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { teardown(); stopSelf(); return START_NOT_STICKY }
            else -> startTun()
        }
        return START_STICKY
    }

    private fun append(line: String) {
        synchronized(sinkLock) { File(filesDir, SINK_FILE).appendText(line + "\n") }
    }

    private fun startTun() {
        if (running) return
        File(filesDir, SINK_FILE).writeText("")  // fresh per run
        val pfd = Builder()
            .setSession("gatepath-test-sink")
            .addAddress(TUN_ADDR, 32)
            .addRoute("0.0.0.0", 0)
            .setMtu(MTU)
            .also { it.addAllowedApplication(packageName) }
            .establish() ?: run { Log.e(TAG, "establish() null — VPN not authorized?"); return }
        tun = pfd
        running = true
        Thread { readLoop(FileInputStream(pfd.fileDescriptor)) }.apply { isDaemon = true }.start()
        Log.i(TAG, "test VPN sink established")
    }

    private fun readLoop(input: FileInputStream) {
        val buf = ByteArray(MTU)
        while (running) {
            val n = try { input.read(buf) } catch (e: Exception) { break }
            if (n <= 0) continue
            parseIpv4(buf, n)?.let { append(it) }
        }
    }

    private fun parseIpv4(pkt: ByteArray, len: Int): String? {
        if (len < 20 || ((pkt[0].toInt() ushr 4) and 0xF) != 4) return null
        val ihl = (pkt[0].toInt() and 0xF) * 4
        if (len < ihl) return null
        val proto = pkt[9].toInt() and 0xFF
        val dst = "${pkt[16].toInt() and 0xFF}.${pkt[17].toInt() and 0xFF}." +
                  "${pkt[18].toInt() and 0xFF}.${pkt[19].toInt() and 0xFF}"
        val dport = if ((proto == 6 || proto == 17) && len >= ihl + 4)
            ((pkt[ihl + 2].toInt() and 0xFF) shl 8) or (pkt[ihl + 3].toInt() and 0xFF) else -1
        return JSONObject()
            .put("dst", dst).put("port", dport)
            .put("proto", when (proto) { 6 -> "TCP"; 17 -> "UDP"; else -> "IP$proto" })
            .put("t", System.currentTimeMillis() / 1000.0)
            .toString()
    }

    private fun teardown() {
        running = false
        try { tun?.close() } catch (_: Exception) {}
        tun = null
    }

    override fun onDestroy() { teardown(); super.onDestroy() }

    companion object {
        private const val TAG = "GatepathTestVpn"
        const val ACTION_START = "cc.grepon.gatepath.testvpn.START"
        const val ACTION_STOP = "cc.grepon.gatepath.testvpn.STOP"
        const val SINK_FILE = "vpn-sink.jsonl"
        private const val TUN_ADDR = "10.111.0.2"
        private const val MTU = 1500
    }
}
