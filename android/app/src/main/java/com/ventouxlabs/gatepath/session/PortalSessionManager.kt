package com.ventouxlabs.gatepath.session

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

    /** Detected → Active. [openedUtc] is captured by the caller (ISO-8601 UTC). */
    fun openPortal(current: PortalSession, openedUtc: String): PortalSession {
        if (current !is PortalSession.Detected) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Active(
            portalUrl = current.portalUrl,
            openedUtc = openedUtc,
        )
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

    /** Active → Completed(PORTAL_COMPLETED). [closedUtc] supplied by caller. */
    fun completePortal(current: PortalSession, closedUtc: String): PortalSession {
        if (current !is PortalSession.Active) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Completed(
            closeReason = CloseReason.PORTAL_COMPLETED,
            openedUtc = current.openedUtc,
            closedUtc = closedUtc,
            portalUrl = current.portalUrl,
            blockedNavigationAttempts = current.blockedNavigationAttempts,
            blockedResourceRequests = current.blockedResourceRequests,
        )
    }

    /**
     * Active → Completed(USER_DISMISSED).
     * Detected → Completed(ABORTED_PRE_ACTIVE) — the user dismissed before the
     * portal window opened, so the audit entry is honestly classified as a
     * pre-Active abort rather than a USER_DISMISSED of a session that never
     * was. [closedUtc] is also used as the synthetic openedUtc for pre-Active
     * dismisses so the audit log invariant holds.
     * Monitoring → Completed(ABORTED_PRE_ACTIVE) with empty portalUrl (no URL
     * was ever observed) — the writer's `portal_domain` validation will reject
     * this, so callers must avoid this path.
     */
    fun dismiss(current: PortalSession, closedUtc: String): PortalSession {
        return when (current) {
            is PortalSession.Active -> PortalSession.Completed(
                closeReason = CloseReason.USER_DISMISSED,
                openedUtc = current.openedUtc,
                closedUtc = closedUtc,
                portalUrl = current.portalUrl,
                blockedNavigationAttempts = current.blockedNavigationAttempts,
                blockedResourceRequests = current.blockedResourceRequests,
            )
            is PortalSession.Detected -> PortalSession.Completed(
                closeReason = CloseReason.ABORTED_PRE_ACTIVE,
                openedUtc = closedUtc,
                closedUtc = closedUtc,
                portalUrl = current.portalUrl,
            )
            is PortalSession.Monitoring -> PortalSession.Completed(
                closeReason = CloseReason.ABORTED_PRE_ACTIVE,
                openedUtc = closedUtc,
                closedUtc = closedUtc,
                portalUrl = "",
            )
            else -> {
                rejectedTransitions.incrementAndGet()
                current
            }
        }
    }

    /** Active → Completed(TIMEOUT). [closedUtc] supplied by caller. */
    fun timeout(current: PortalSession, closedUtc: String): PortalSession {
        if (current !is PortalSession.Active) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Completed(
            closeReason = CloseReason.TIMEOUT,
            openedUtc = current.openedUtc,
            closedUtc = closedUtc,
            portalUrl = current.portalUrl,
            blockedNavigationAttempts = current.blockedNavigationAttempts,
            blockedResourceRequests = current.blockedResourceRequests,
        )
    }

    /**
     * Active → Completed(ERROR). Detected / Monitoring → Completed(ABORTED_PRE_ACTIVE).
     * Anything else (Idle, already-Completed, already-Error) → Error.
     * Eliminates the prior path that wrote audit entries with empty timestamps
     * for pre-Active errors.
     */
    fun error(current: PortalSession, closedUtc: String, message: String): PortalSession {
        return when (current) {
            is PortalSession.Active -> PortalSession.Completed(
                closeReason = CloseReason.ERROR,
                openedUtc = current.openedUtc,
                closedUtc = closedUtc,
                portalUrl = current.portalUrl,
                blockedNavigationAttempts = current.blockedNavigationAttempts,
                blockedResourceRequests = current.blockedResourceRequests,
            )
            is PortalSession.Detected -> PortalSession.Completed(
                closeReason = CloseReason.ABORTED_PRE_ACTIVE,
                openedUtc = closedUtc,
                closedUtc = closedUtc,
                portalUrl = current.portalUrl,
            )
            is PortalSession.Monitoring -> PortalSession.Completed(
                closeReason = CloseReason.ABORTED_PRE_ACTIVE,
                openedUtc = closedUtc,
                closedUtc = closedUtc,
                portalUrl = "",
            )
            else -> PortalSession.Error(message = message)
        }
    }

    /** Error → Idle (reset after error) */
    fun reset(current: PortalSession): PortalSession {
        if (current !is PortalSession.Error && current !is PortalSession.Completed) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return PortalSession.Idle
    }
}
