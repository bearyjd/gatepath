package com.ventouxlabs.gatepath

import android.app.Application
import android.util.Log
import androidx.lifecycle.ProcessLifecycleOwner
import com.ventouxlabs.gatepath.audit.AuditLog
import com.ventouxlabs.gatepath.di.ProcessBindingEntryPoint
import com.ventouxlabs.gatepath.network.AndroidProcessBinding
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.android.HiltAndroidApp

@Suppress("unused") // Application class referenced from AndroidManifest.

private const val TAG = "GatepathApp"

@HiltAndroidApp
class GatepathApplication : Application() {

    private val processBinding: AndroidProcessBinding by lazy {
        EntryPointAccessors.fromApplication(this, ProcessBindingEntryPoint::class.java).processBinding()
    }

    override fun onCreate() {
        super.onCreate()
        AuditLog.init(filesDir)
        // Watchdog: release every owner's lease (and force-bind null) when the
        // WHOLE app goes to background. ProcessLifecycleOwner debounces across
        // per-Activity pause/resume transitions (rotation, single-task switch,
        // intent-launched activity), so routine in-app navigation does NOT
        // trigger a clear. See SECURITY_MODEL.md "Caveat — bindProcessToNetwork
        // is process-wide" for the leak class this defends against, and
        // ProcessBinding's KDoc for why releaseAll() is safe to call even with
        // a borrow (the monitor's probe) in flight.
        ProcessLifecycleOwner.get().lifecycle.addObserver(
            BindWatchdog {
                clearProcessNetworkBinding("ProcessLifecycleOwner.onStop (app backgrounded)")
            }
        )
    }

    override fun onTerminate() {
        // Belt-and-braces: clear the binding on orderly shutdown. Android
        // rarely calls this, but when it does we want the process to leave
        // a clean kernel state.
        clearProcessNetworkBinding("onTerminate")
        super.onTerminate()
    }

    private fun clearProcessNetworkBinding(reason: String) {
        runCatching {
            processBinding.releaseAll()
            Log.d(TAG, "Released all process-binding leases ($reason)")
        }.onFailure { ex ->
            Log.w(TAG, "Failed to release process-binding leases ($reason): ${ex.message}")
        }
    }
}
