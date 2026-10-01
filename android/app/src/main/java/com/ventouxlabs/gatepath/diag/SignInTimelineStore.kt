package com.ventouxlabs.gatepath.diag

/**
 * Hands out one [SignInTimeline] per sign-in. The same network within
 * [windowMillis] continues the same timeline, so a fold/rotation rebuild of
 * the sign-in screen, or reopening it from Android's notification, appends to
 * one record instead of starting over. A different network, or a stale one,
 * starts fresh. Process-lifetime only: nothing is written to disk until the
 * user exports.
 */
class SignInTimelineStore(
    private val clock: () -> Long = System::currentTimeMillis,
    private val windowMillis: Long = DEFAULT_WINDOW_MILLIS,
) {
    private var current: SignInTimeline? = null

    @Synchronized
    fun forNetwork(network: String): SignInTimeline {
        val existing = current
        if (existing != null && existing.network == network && clock() - existing.startedAtMillis <= windowMillis) {
            return existing
        }
        return SignInTimeline(network, clock).also { current = it }
    }

    companion object {
        const val DEFAULT_WINDOW_MILLIS = 30 * 60_000L

        /** The app-wide store; the sign-in screen and its WebView share it. */
        val shared = SignInTimelineStore()
    }
}
