package cc.grepon.gatepath.session

/**
 * Immutable sealed class hierarchy modelling the portal session state machine.
 *
 * States: Idle → Monitoring → Detected → Active → Completed | Error
 *         Any state → (user dismisses) → Completed with USER_DISMISSED
 */
sealed class PortalSession {

    /** No active monitoring. Initial state. */
    data object Idle : PortalSession()

    /** Connectivity probing is underway; no captive portal detected yet. */
    data object Monitoring : PortalSession()

    /**
     * A captive portal redirect was detected at [portalUrl].
     * VPN check may still be pending before showing the warning.
     */
    data class Detected(
        val portalUrl: String,
    ) : PortalSession()

    /**
     * The portal WebView is open. [portalUrl] is the validated portal URL.
     * Counters are tracked here and carried forward to Completed.
     */
    data class Active(
        val portalUrl: String,
        val blockedNavigationAttempts: Int = 0,
        val blockedResourceRequests: Int = 0,
    ) : PortalSession()

    /**
     * Session has ended (successfully, dismissed, timed out, or errored).
     */
    data class Completed(
        val closeReason: CloseReason,
        val blockedNavigationAttempts: Int = 0,
        val blockedResourceRequests: Int = 0,
    ) : PortalSession()

    /** Something went wrong; details in [message]. */
    data class Error(val message: String) : PortalSession()
}

/**
 * Why the portal session was closed. Values must match the audit log schema enum.
 * Source of truth: `docs/audit_log_schema.json` `close_reason_enum`.
 */
enum class CloseReason(val schemaValue: String) {
    PORTAL_COMPLETED("portal_completed"),
    USER_DISMISSED("user_dismissed"),
    TIMEOUT("timeout"),
    ERROR("error"),

    /**
     * Session was terminated before the portal window opened (e.g. network lost
     * during Detected phase). Audit entry has `duration_seconds=0` and
     * `session_opened_utc == session_closed_utc`.
     */
    ABORTED_PRE_ACTIVE("aborted_pre_active"),
}
