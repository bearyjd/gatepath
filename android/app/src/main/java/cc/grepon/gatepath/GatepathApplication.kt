package cc.grepon.gatepath

import android.app.Activity
import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.os.Bundle
import android.util.Log
import cc.grepon.gatepath.audit.AuditLog
import dagger.hilt.android.HiltAndroidApp

private const val TAG = "GatepathApp"

@HiltAndroidApp
class GatepathApplication : Application() {

    override fun onCreate() {
        super.onCreate()
        AuditLog.init(filesDir)
        // Watchdog: clear any leftover process-network binding when no
        // foreground activity is active. SECURITY_MODEL.md "Caveat —
        // bindProcessToNetwork is process-wide" describes the latent leak
        // class this defends against.
        registerActivityLifecycleCallbacks(BindWatchdog(this))
    }

    override fun onTerminate() {
        // Belt-and-braces: clear the binding on orderly shutdown. Android
        // rarely calls this, but when it does we want the process to leave a
        // clean kernel state.
        clearProcessNetworkBinding(this, "onTerminate")
        super.onTerminate()
    }

    /**
     * Activity lifecycle watchdog. Counts foreground activities and clears
     * the process Network binding when the count reaches zero (i.e., the
     * user backgrounded the whole app). The portal WebView's normal close
     * path also clears the binding via `DisposableEffect.onDispose`; this
     * watchdog is the recovery for crash / abnormal-exit paths.
     */
    private class BindWatchdog(private val app: Application) :
        ActivityLifecycleCallbacks {

        private var resumedCount: Int = 0

        override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) = Unit
        override fun onActivityStarted(activity: Activity) = Unit
        override fun onActivityResumed(activity: Activity) {
            resumedCount += 1
        }

        override fun onActivityPaused(activity: Activity) {
            resumedCount = (resumedCount - 1).coerceAtLeast(0)
            if (resumedCount == 0) {
                clearProcessNetworkBinding(app, "no resumed activities")
            }
        }

        override fun onActivityStopped(activity: Activity) = Unit
        override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
        override fun onActivityDestroyed(activity: Activity) = Unit
    }
}

/**
 * Idempotent: calling with no active binding is a no-op. Top-level so the
 * Activity-lifecycle inner class can call it without a Context dependency.
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
