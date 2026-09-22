package com.ventouxlabs.gatepath.network

import android.util.Log
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

private const val TAG = "ProcessBinding"

/**
 * Opaque token for one acquired binding, for a [ProcessBinding] over network
 * type [N]. Compared by identity only. Top-level (not nested in
 * [ProcessBinding]) so a caller can name the type without qualifying it
 * against a specific [ProcessBinding] instantiation — see
 * `AndroidNetworkBinder.kt`'s `AndroidProcessBinding` typealias for the one
 * production instantiation's spelling.
 */
class Lease<N : Any> internal constructor(internal val network: N)

/**
 * Single owner of the process-wide `bindProcessToNetwork` slot.
 *
 * `bindProcessToNetwork` is one process-global value with no built-in
 * ownership tracking. Historically four call sites raced to save/bind/restore
 * it independently (see `SECURITY_MODEL.md`'s "Caveat" section):
 *
 *  - `CaptivePortalMonitor`'s probe saved the current binding, bound its
 *    probe network, and restored the saved value in a `finally` — a probe in
 *    flight when a screen released its own binding restored a value nobody
 *    owned, and two concurrent probes interleaved their save/restore.
 *  - `GatepathWebView` bound on entry and nulled on dispose, with no way to
 *    tell whether another owner was still using the slot.
 *  - `CaptivePortalActivity` bound in `onCreate` and released in `onDestroy`
 *    only if the slot still held *its* network — which cannot distinguish
 *    two owners of the same network from each other.
 *  - `GatepathApplication` nulled the slot on whole-app background.
 *
 * This class replaces all four call sites with two roles:
 *
 *  - **Owners** (WebView screens, the handoff activity) hold a [Lease] from
 *    [acquire]. Leases form a stack; the bound network always follows the
 *    top. [release] removes a lease wherever it sits — not just the top — so
 *    an out-of-order release (e.g. the handoff activity releasing while
 *    `MainActivity`'s WebView is still live) leaves the still-live owner's
 *    binding in place instead of nulling a slot someone else needs.
 *  - **Borrowers** (the monitor's probe) call [borrow], which pins the slot
 *    to the borrow's network for the duration of the block and restores it
 *    to the *current* top of the owner stack afterwards — never to a value
 *    saved before the block ran, which is what let a stale restore happen.
 *
 * Trade-off: an owner [acquire] or [release] that happens *while a borrow is
 * in flight* updates the owner stack immediately, but the underlying bind is
 * not re-applied until the borrow ends. In practice this means a WebView's
 * traffic during the few seconds of a probe keeps following the probe's
 * network, exactly as it did under the old per-caller save/restore — but
 * unlike the old code, the process can never end up bound with no owner: the
 * slot is always re-applied to the real current state (the owner stack, or
 * the probe network) rather than to a snapshot that may already be stale.
 * One corollary: [acquire] during a borrow cannot test the real bind without
 * disturbing the borrow's pinned network, so it always succeeds and pushes a
 * lease; if the deferred bind would actually have been refused, that failure
 * surfaces only once the borrow ends (the slot then simply isn't what the
 * stack says it is) and is never reported back to that `acquire` caller —
 * this weakens the "a refused bind never claims the slot" guarantee during
 * the borrow window specifically; see `ProcessBindingTest` for the pinned
 * behavior and the stage-1 report for why this was left for stage 2 to
 * revisit. [releaseAll] is the one exception to the deferral: it always
 * binds null immediately, borrow or not, because it is a leak defense that
 * cannot wait.
 *
 * Thread-safety: [acquire], [release] and [releaseAll] are called from the
 * main thread and are guarded by a plain lock. [borrow] runs on a background
 * dispatcher and serialises concurrent borrows on a [Mutex] so two probes
 * never interleave their bind calls.
 *
 * Generic over the network type [N]: production always instantiates this
 * with [android.net.Network] (see `AndroidNetworkBinder.kt`'s
 * `AndroidProcessBinding` typealias), but this file stays free of that
 * Android SDK type so it can be exercised by `run-jvm-tests.sh`'s no-SDK
 * suite. `android.net.Network`'s android.jar stub has a package-private
 * constructor and a stub `equals`/`hashCode` under Gradle unit tests (see
 * CLAUDE.md's "two paths can disagree" note), so `ProcessBindingTest` uses a
 * plain data class with real equality instead of ever constructing one.
 */
class ProcessBinding<N : Any>(private val binder: NetworkBinder<N>) {

    private val lock = Any()
    private val stack = ArrayList<Lease<N>>()
    private val borrowMutex = Mutex()
    private var borrowing = false

    /**
     * Binds [network] and pushes a new [Lease] onto the owner stack. Returns
     * null and leaves the stack untouched when the binder refuses the bind
     * (e.g. `EPERM` under a secure VPN), so a refused bind never claims the
     * slot. See the class KDoc for the deferred-bind behavior while a
     * [borrow] is in flight.
     */
    fun acquire(network: N): Lease<N>? {
        synchronized(lock) {
            if (borrowing) {
                val lease = Lease(network)
                stack.add(lease)
                return lease
            }
            if (!binder.bind(network)) return null
            val lease = Lease(network)
            stack.add(lease)
            return lease
        }
    }

    /**
     * Removes [lease] from the owner stack, wherever it sits. Releasing a
     * lease that is not on the stack (already released, or stale after
     * [releaseAll]) is a no-op. Rebinds to the new top, or null when the
     * stack is empty, unless a [borrow] is in flight, in which case the
     * rebind is deferred until it ends.
     */
    fun release(lease: Lease<N>) {
        synchronized(lock) {
            stack.remove(lease)
            if (borrowing) return
            applyTop()
        }
    }

    /**
     * Clears the owner stack and binds null immediately — including while a
     * [borrow] is in flight. This is the whole-app-background and
     * `onTerminate` leak defense: unlike [acquire]/[release], it must not
     * wait for a borrow to finish, since a borrow may not finish before the
     * process dies. Yanking a probe's network mid-flight is the correct
     * outcome here. The borrow's own restore afterwards binds null again
     * against an empty stack, which is a harmless no-op.
     */
    fun releaseAll() {
        synchronized(lock) {
            stack.clear()
            binder.bind(null)
        }
    }

    /** The binder's own view of the current binding, for diagnostics/logging. */
    fun current(): N? = binder.current()

    /**
     * Pins the slot to [network] for the duration of [block], serialised
     * against other borrows on [borrowMutex] (which is not reentrant — only
     * the monitor's probe ever borrows, and a nested borrow would deadlock).
     * Afterwards the slot is restored to the current top of the owner stack
     * (null if empty) — never to a value saved before [block] ran.
     *
     * Setting [borrowing] and calling [NetworkBinder.bind] happen inside the
     * same `synchronized(lock)` block, and that block is inside the `try`:
     * two failure modes this closes. First, an owner [release] or
     * [releaseAll] landing between "set the flag" and "call bind" would
     * otherwise be silently undone by the bind that follows — the owner's
     * `null` would get immediately overwritten by the borrow's network.
     * Second, if `bind` itself throws, [borrowing] would otherwise stay true
     * forever: every later [acquire] would push a lease without ever calling
     * `bind` for it (fail-open) because it would keep taking the
     * already-borrowing branch. With both inside the `try`, the `finally`
     * (`borrowing = false; applyTop()`) always runs and self-heals the slot
     * back to the real owner stack even on that failure path.
     *
     * A whole-app-background [releaseAll] that lands *during* this call still
     * leaves the probe bound to [network] until [block] returns — backgrounding
     * is not termination, so letting the in-flight probe finish on its own
     * network is acceptable; [releaseAll]'s guarantee is that the slot is
     * `null` immediately afterward if no borrow is holding it open, which
     * still holds here once [block] completes and `applyTop()` runs.
     *
     * Unlike [acquire], which treats `bind`'s Boolean as the signal a caller
     * needs (null lease on refusal), this discards it deliberately: the one
     * borrower is `CaptivePortalMonitor`'s probe, which scopes its own socket
     * via `Network.openConnection` rather than depending on the process-wide
     * bind, so a refused bind here doesn't change what the probe measures.
     * The refusal is still logged so it's visible in diagnostics.
     */
    suspend fun <T> borrow(network: N, block: suspend () -> T): T {
        return borrowMutex.withLock {
            try {
                synchronized(lock) {
                    borrowing = true
                    if (!binder.bind(network)) {
                        Log.w(TAG, "borrow($network): bind refused; probe socket is self-scoped so this is not acted on")
                    }
                }
                block()
            } finally {
                synchronized(lock) {
                    borrowing = false
                    applyTop()
                }
            }
        }
    }

    /** Must be called while holding [lock]. */
    private fun applyTop() {
        binder.bind(stack.lastOrNull()?.network)
    }
}
