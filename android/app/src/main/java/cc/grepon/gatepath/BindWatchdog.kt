package cc.grepon.gatepath

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
 * Visibility: `internal` (not private) so the JVM test can construct it
 * without reflection.
 */
internal class BindWatchdog(
    private val onAppBackgrounded: () -> Unit,
) : DefaultLifecycleObserver {

    override fun onStop(owner: LifecycleOwner) {
        onAppBackgrounded()
    }
}
