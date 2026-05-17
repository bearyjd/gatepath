package cc.grepon.gatepath

import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Pure-JVM tests for [BindWatchdog].
 *
 * Verifies the structural guarantee documented in SECURITY_MODEL.md:
 * the watchdog fires *only* when the whole app goes to background
 * (ON_STOP), and routine in-app navigation transitions (ON_PAUSE → ON_RESUME
 * without an intervening ON_STOP) do NOT fire it.
 *
 * Closes BLOCKERS.md KNOWN-AND-001.
 */
class BindWatchdogTest {

    /**
     * Owns a [LifecycleRegistry] driven manually. `createUnsafe` skips the
     * main-thread assertion that `ArchTaskExecutor` makes on Android — required
     * to feed lifecycle events from a plain-JVM test thread.
     */
    private class TestOwner : LifecycleOwner {
        val registry: LifecycleRegistry = LifecycleRegistry.createUnsafe(this)
        override val lifecycle: Lifecycle get() = registry
    }

    private data class Fixture(val owner: TestOwner, val fires: IntArray) {
        val fireCount: Int get() = fires[0]
    }

    private fun fixture(): Fixture {
        val owner = TestOwner()
        val fires = IntArray(1)
        owner.registry.addObserver(BindWatchdog { fires[0]++ })
        return Fixture(owner, fires)
    }

    @Test
    fun `routine pause-resume without stop does NOT fire the lambda`() {
        val f = fixture()
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_START)
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
        assertEquals(0, f.fireCount)
    }

    @Test
    fun `full cycle ending in stop fires the lambda exactly once`() {
        val f = fixture()
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_START)
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
        assertEquals(1, f.fireCount)
    }

    @Test
    fun `repeated foreground-background cycles fire once per stop`() {
        val f = fixture()
        f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)
        repeat(3) {
            f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_START)
            f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
            f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
            f.owner.registry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
        }
        assertEquals(3, f.fireCount)
    }
}
