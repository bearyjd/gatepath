package cc.grepon.gatepath.session

import java.util.concurrent.atomic.AtomicInteger

/**
 * Pure-Kotlin state machine for portal sessions.
 *
 * All transition methods return a NEW [PortalSession] instance — never mutate in place.
 * Invalid transitions return the current state unchanged and increment [rejectedTransitions].
 * Thread-safety: each call is atomic w.r.t. the returned new state; callers own concurrency.
 */
class PortalSessionManager {

    /** Count of transition attempts that were rejected (invalid for the current state). */
    val rejectedTransitions: AtomicInteger = AtomicInteger(0)

    /** Idle → Monitoring */
    fun startMonitoring(current: PortalSession): PortalSession {
        if (current !is PortalSession.Idle) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Monitoring
    }

    /** Monitoring → Detected */
    fun portalDetected(current: PortalSession, portalUrl: String): PortalSession {
        if (current !is PortalSession.Monitoring) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Detected(portalUrl = portalUrl)
    }

    /** Detected → Active */
    fun openPortal(current: PortalSession): PortalSession {
        if (current !is PortalSession.Detected) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Active(portalUrl = current.portalUrl)
    }

    /**
     * Active → Active: record one blocked navigation.
     * Attempting this on a non-Active state is rejected.
     */
    fun recordBlockedNavigation(current: PortalSession): PortalSession {
        if (current !is PortalSession.Active) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return current.copy(blockedNavigationAttempts = current.blockedNavigationAttempts + 1)
    }

    /**
     * Active → Active: record one blocked resource request.
     * Attempting this on a non-Active state is rejected.
     */
    fun recordBlockedResource(current: PortalSession): PortalSession {
        if (current !is PortalSession.Active) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return current.copy(blockedResourceRequests = current.blockedResourceRequests + 1)
    }

    /** Active → Completed(PORTAL_COMPLETED) */
    fun completePortal(current: PortalSession): PortalSession {
        if (current !is PortalSession.Active) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Completed(
            closeReason = CloseReason.PORTAL_COMPLETED,
            blockedNavigationAttempts = current.blockedNavigationAttempts,
            blockedResourceRequests = current.blockedResourceRequests,
        )
    }

    /** Active | Detected | Monitoring → Completed(USER_DISMISSED) */
    fun dismiss(current: PortalSession): PortalSession {
        return when (current) {
            is PortalSession.Active -> PortalSession.Completed(
                closeReason = CloseReason.USER_DISMISSED,
                blockedNavigationAttempts = current.blockedNavigationAttempts,
                blockedResourceRequests = current.blockedResourceRequests,
            )
            is PortalSession.Detected, is PortalSession.Monitoring ->
                PortalSession.Completed(closeReason = CloseReason.USER_DISMISSED)
            else -> {
                rejectedTransitions.incrementAndGet()
                current
            }
        }
    }

    /** Active → Completed(TIMEOUT) */
    fun timeout(current: PortalSession): PortalSession {
        if (current !is PortalSession.Active) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Completed(
            closeReason = CloseReason.TIMEOUT,
            blockedNavigationAttempts = current.blockedNavigationAttempts,
            blockedResourceRequests = current.blockedResourceRequests,
        )
    }

    /** Any → Error */
    fun error(current: PortalSession, message: String): PortalSession =
        PortalSession.Error(message = message)

    /** Error → Idle (reset after error) */
    fun reset(current: PortalSession): PortalSession {
        if (current !is PortalSession.Error && current !is PortalSession.Completed) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Idle
    }
}
