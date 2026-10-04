package com.ventouxlabs.gatepath.network

/**
 * The answer the system handoff (`CaptivePortalActivity`) gives Android through
 * its `CaptivePortal` token when the sign-in screen goes away.
 *
 * There is deliberately no "ignore" answer. `CaptivePortal.ignoreNetwork()`
 * means "the user does not want this network": Android tears the Wi-Fi down,
 * disables auto-join for it, records a no-internet report and stops showing
 * the sign-in notification. Closing a screen is not that decision. On a real
 * hotel portal (2026-09-30) a back gesture after a *successful* sign-in made
 * the old code send it, and the phone dropped the Wi-Fi one second later and
 * left it `NETWORK_SELECTION_DISABLED_NO_INTERNET_TEMPORARY`.
 *
 * `reportCaptivePortalDismissed()` is the neutral answer: Android re-checks the
 * network at once. Signed in, it validates; still captive, the portal is
 * detected again and the sign-in notification comes back. Both were verified
 * on the same hotel portal after this change.
 *
 * This only covers exits that destroy the screen. If the process dies before
 * `onDestroy` runs, nothing is answered and Android re-checks on its own
 * schedule, as it does for its own sign-in app.
 */
enum class CaptivePortalReply {
    /** Ask Android to re-check the network now (`reportCaptivePortalDismissed()`). */
    DISMISSED,

    /** Send nothing: an answer already went out, or a rebuilt screen will send it. */
    NONE,
    ;

    companion object {
        /**
         * The reply for a sign-in screen that is being destroyed.
         *
         * @param alreadyReported an answer already went out (the Dismiss button).
         * @param changingConfigurations the screen is only being rebuilt, for a
         *   configuration change it does not handle in place (a language or
         *   font-size switch; folds and rotations no longer rebuild it). The
         *   rebuilt screen receives the same intent, and so the same token, and
         *   answers for itself.
         */
        fun onDestroy(alreadyReported: Boolean, changingConfigurations: Boolean): CaptivePortalReply =
            if (alreadyReported || changingConfigurations) NONE else DISMISSED
    }
}
