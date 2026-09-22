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

    /**
     * Result of [reenter]. Callers must branch on this rather than inspecting
     * the returned session's type — a rejected reenter from a session that is
     * already [PortalSession.Detected] returns that same [PortalSession.Detected]
     * unchanged, which is indistinguishable from an accepted transition if a
     * caller only checks `is PortalSession.Detected`.
     */
    sealed interface ReenterResult {
        /** The transition happened; [session] is the new [PortalSession.Detected]. */
        data class Accepted(val session: PortalSession.Detected) : ReenterResult

        /** The transition was invalid for [current]; the session is untouched. */
        data class Rejected(val current: PortalSession) : ReenterResult
    }

    /**
     * Completed | Monitoring → Detected: the user asked to sign in again after
     * dismissing, from the confinement card that stays on screen.
     *
     * [portalDetected] cannot serve this: it accepts only [PortalSession.Monitoring],
     * and a dismiss leaves the session [PortalSession.Completed], which is
     * precisely the state "Sign in here" exists to recover from. Re-entering
     * from [PortalSession.Active] or [PortalSession.Detected] is rejected —
     * there is already a window open (or about to be).
     */
    fun reenter(current: PortalSession, portalUrl: String): ReenterResult {
        return when (current) {
            is PortalSession.Completed, is PortalSession.Monitoring ->
                ReenterResult.Accepted(PortalSession.Detected(portalUrl = portalUrl))
            else -> {
                rejectedTransitions.incrementAndGet()
                ReenterResult.Rejected(current)
            }
        }
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

    /**
     * Active → Active: record one TLS certificate error that was proceeded past
     * on the portal host. Unlike the counters above this one records a *trust
     * grant*, not an observation — a non-zero value in the audit log means the
     * session rendered a page whose certificate did not validate.
     * Attempting this on a non-Active state is rejected.
     */
    fun recordTlsCertErrorBypassed(current: PortalSession): PortalSession {
        if (current !is PortalSession.Active) {
            rejectedTransitions.incrementAndGet()
            return current
        }
        return current.copy(tlsCertErrorsBypassed = current.tlsCertErrorsBypassed + 1)
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
            tlsCertErrorsBypassed = current.tlsCertErrorsBypassed,
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
                tlsCertErrorsBypassed = current.tlsCertErrorsBypassed,
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
            tlsCertErrorsBypassed = current.tlsCertErrorsBypassed,
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
                tlsCertErrorsBypassed = current.tlsCertErrorsBypassed,
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
