package cc.grepon.gatepath

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.util.Log
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import cc.grepon.gatepath.audit.AuditLog
import dagger.hilt.android.HiltAndroidApp

private const val TAG = "GatepathApp"

@HiltAndroidApp
class GatepathApplication : Application() {

    override fun onCreate() {
        super.onCreate()
        AuditLog.init(filesDir)
        // Watchdog: clear any leftover process-network binding when the WHOLE
        // app goes to background. ProcessLifecycleOwner debounces across
        // per-Activity pause/resume transitions (rotation, single-task switch,
        // intent-launched activity), so routine in-app navigation does NOT
        // trigger a clear. See SECURITY_MODEL.md "Caveat — bindProcessToNetwork
        // is process-wide" for the leak class this defends against.
        ProcessLifecycleOwner.get().lifecycle.addObserver(BindWatchdog(this))
    }

    override fun onTerminate() {
        // Belt-and-braces: clear the binding on orderly shutdown. Android
        // rarely calls this, but when it does we want the process to leave
        // a clean kernel state.
        clearProcessNetworkBinding(this, "onTerminate")
        super.onTerminate()
    }
}

/**
 * Whole-app foreground/background watchdog. Clears the process-network
 * binding only when the entire app goes to background — never on per-Activity
 * pause during in-app navigation.
 *
 * `onStop` fires once after the last visible Activity has been stopped (with
 * an internal debounce of ~700ms in androidx.lifecycle:lifecycle-process), so
 * activity transitions within the app do not trip this observer.
 */
private class BindWatchdog(private val app: Application) : DefaultLifecycleObserver {

    override fun onStop(owner: LifecycleOwner) {
        clearProcessNetworkBinding(app, "ProcessLifecycleOwner.onStop (app backgrounded)")
    }
}

/**
 * Idempotent: calling with no active binding is a no-op. Top-level so the
 * watchdog can call it without a Context dependency.
 */
private fun clearProcessNetworkBinding(ctx: Context, reason: String) {
    runCatching {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        cm?.bindProcessToNetwork(null)
        Log.d(TAG, "Cleared process network binding ($reason)")
    }.onFailure { ex ->
        Log.w(TAG, "Failed to clear process network binding ($reason): ${ex.message}")
    }
}
