package com.ventouxlabs.gatepath

import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner

/**
 * Whole-app foreground/background watchdog. Clears the process-network
 * binding only when the entire app goes to background — never on per-Activity
 * pause during in-app navigation.
 *
 * `onStop` fires once after the last visible Activity has been stopped (with
 * an internal debounce of ~700ms in androidx.lifecycle:lifecycle-process), so
 * activity transitions within the app do not trip this observer.
 *
 * Constructor takes a lambda instead of an Application reference so the
 * observer is unit-testable on plain JVM: tests pass a recording lambda and
 * feed lifecycle events via `LifecycleRegistry`. See `BindWatchdogTest.kt`.
 *
 */
internal class BindWatchdog(
    private val onAppBackgrounded: () -> Unit,
    private val onAppForegrounded: () -> Unit = {},
) : DefaultLifecycleObserver {

    /**
     * Fires when the app returns to the foreground (and on first launch,
     * where the callback must be a no-op). Without it, whatever
     * [onAppBackgrounded] tore down is never put back for the WebView that
     * outlived the background — see issue #189.
     */
    override fun onStart(owner: LifecycleOwner) {
        onAppForegrounded()
    }

    override fun onStop(owner: LifecycleOwner) {
        onAppBackgrounded()
    }
}
