package com.ventouxlabs.gatepath.network

/**
 * Why [ConfinementState.Unknown] fired. Kept as a typed reason rather than
 * re-deriving it elsewhere from [ConfinementState.Unknown.bindError]'s free
 * text, so a downstream carve-out (e.g. the handoff screen offering
 * "try signing in anyway" for a bind that actually succeeded, even under a
 * VPN) doesn't have to pattern-match error strings.
 */
enum class UnknownReason {
    /**
     * The bound probe got a 204 from our probe endpoint. That proves the
     * probe's socket reached the network over the Wi-Fi, but not that there
     * is no portal: venue walled gardens let connectivitycheck.gstatic.com
     * through before sign-in (seen on a real hotel portal, 2026-10-01). In the
     * system handoff, Android's own portal verdict plus a held process bind
     * turn this into Confined instead; see [classify]. [classify] takes this
     * branch from [com.ventouxlabs.gatepath.network.ProbeResult.Validated].
     */
    BOUND_VALIDATED,

    /**
     * The bound probe failed with an error whose [ProbeErrorReason] carries
     * no typed EPERM/EACCES verdict (connection refused, unreachable,
     * timeout, or otherwise unclassified).
     */
    PROBE_ERROR,
}
