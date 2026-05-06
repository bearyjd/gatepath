package cc.grepon.gatepath

import cc.grepon.gatepath.session.CloseReason
import cc.grepon.gatepath.session.PortalSession
import cc.grepon.gatepath.session.PortalSessionManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Pure-JVM tests for [PortalSessionManager] state transitions. No Android SDK required.
 *
 * The manager is timestamp-pure: callers supply the ISO-8601 UTC strings, so
 * tests use deterministic literals like "2026-05-06T12:00:00Z".
 */
class SessionStateTest {

    private lateinit var manager: PortalSessionManager

    @Before
    fun setUp() {
        manager = PortalSessionManager()
    }

    private val opened = "2026-05-06T12:00:00Z"
    private val closed = "2026-05-06T12:02:42Z"
    private val portalUrl = "http://portal.example.com/login"

    // ── Happy-path transitions ──────────────────────────────────────────────

    @Test
    fun `Idle to Monitoring`() {
        val next = manager.startMonitoring(PortalSession.Idle)
        assertTrue(next is PortalSession.Monitoring)
    }

    @Test
    fun `Monitoring to Detected`() {
        val next = manager.portalDetected(PortalSession.Monitoring, portalUrl)
        assertTrue(next is PortalSession.Detected)
        assertEquals(portalUrl, (next as PortalSession.Detected).portalUrl)
    }

    @Test
    fun `Detected to Active carries portalUrl and openedUtc`() {
        val detected = PortalSession.Detected(portalUrl)
        val next = manager.openPortal(detected, opened)
        assertTrue(next is PortalSession.Active)
        val active = next as PortalSession.Active
        assertEquals(portalUrl, active.portalUrl)
        assertEquals(opened, active.openedUtc)
    }

    @Test
    fun `Active records blocked navigations immutably`() {
        val active = PortalSession.Active(portalUrl, opened)
        val after1 = manager.recordBlockedNavigation(active)
        val after2 = manager.recordBlockedNavigation(after1)
        assertEquals(0, active.blockedNavigationAttempts) // original unchanged
        assertEquals(1, (after1 as PortalSession.Active).blockedNavigationAttempts)
        assertEquals(2, (after2 as PortalSession.Active).blockedNavigationAttempts)
    }

    @Test
    fun `Active records blocked resources immutably`() {
        val active = PortalSession.Active(portalUrl, opened)
        val after = manager.recordBlockedResource(active)
        assertEquals(0, active.blockedResourceRequests)
        assertEquals(1, (after as PortalSession.Active).blockedResourceRequests)
    }

    @Test
    fun `Active to Completed via completePortal preserves timestamps and url`() {
        val active = PortalSession.Active(portalUrl, opened, 2, 5)
        val next = manager.completePortal(active, closed)
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.PORTAL_COMPLETED, completed.closeReason)
        assertEquals(opened, completed.openedUtc)
        assertEquals(closed, completed.closedUtc)
        assertEquals(portalUrl, completed.portalUrl)
        assertEquals(2, completed.blockedNavigationAttempts)
        assertEquals(5, completed.blockedResourceRequests)
    }

    @Test
    fun `Active to Completed via dismiss is USER_DISMISSED`() {
        val active = PortalSession.Active(portalUrl, opened, 1, 3)
        val next = manager.dismiss(active, closed)
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.USER_DISMISSED, completed.closeReason)
        assertEquals(opened, completed.openedUtc)
        assertEquals(closed, completed.closedUtc)
        assertEquals(portalUrl, completed.portalUrl)
        assertEquals(1, completed.blockedNavigationAttempts)
        assertEquals(3, completed.blockedResourceRequests)
    }

    @Test
    fun `Active to Completed via timeout records TIMEOUT and preserves timestamps`() {
        val active = PortalSession.Active(portalUrl, opened, 0, 7)
        val next = manager.timeout(active, closed)
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.TIMEOUT, completed.closeReason)
        assertEquals(opened, completed.openedUtc)
        assertEquals(closed, completed.closedUtc)
        assertEquals(7, completed.blockedResourceRequests)
    }

    @Test
    fun `Active to Completed via error is ERROR phase, not Error state`() {
        val active = PortalSession.Active(portalUrl, opened, 1, 2)
        val next = manager.error(active, closed, "boom")
        // Active errors land in Completed(ERROR), not Error — so the audit
        // log gets a real entry instead of being skipped.
        assertTrue("Expected Completed but got $next", next is PortalSession.Completed)
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.ERROR, completed.closeReason)
        assertEquals(opened, completed.openedUtc)
        assertEquals(closed, completed.closedUtc)
    }

    // ── Pre-Active aborts are reclassified to ABORTED_PRE_ACTIVE ────────────

    @Test
    fun `Detected dismiss is ABORTED_PRE_ACTIVE with synthetic timestamps`() {
        val detected = PortalSession.Detected(portalUrl)
        val next = manager.dismiss(detected, closed)
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.ABORTED_PRE_ACTIVE, completed.closeReason)
        // Synthetic open == close so duration_seconds is 0 and the audit entry
        // doesn't claim a session that never happened.
        assertEquals(closed, completed.openedUtc)
        assertEquals(closed, completed.closedUtc)
        assertEquals(portalUrl, completed.portalUrl)
    }

    @Test
    fun `Detected error is ABORTED_PRE_ACTIVE`() {
        val detected = PortalSession.Detected(portalUrl)
        val next = manager.error(detected, closed, "Network lost")
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.ABORTED_PRE_ACTIVE, completed.closeReason)
        assertEquals(portalUrl, completed.portalUrl)
    }

    @Test
    fun `Monitoring dismiss is ABORTED_PRE_ACTIVE with empty portalUrl`() {
        // No URL was ever observed; portalUrl is empty. Audit writer's
        // portal_domain validation will reject — controller must not write.
        val next = manager.dismiss(PortalSession.Monitoring, closed)
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.ABORTED_PRE_ACTIVE, completed.closeReason)
        assertEquals("", completed.portalUrl)
    }

    @Test
    fun `Idle error returns Error state, not Completed`() {
        val errored = manager.error(PortalSession.Idle, closed, "connection refused")
        assertTrue(errored is PortalSession.Error)
        assertEquals("connection refused", (errored as PortalSession.Error).message)
        val idle = manager.reset(errored)
        assertTrue(idle is PortalSession.Idle)
    }

    @Test
    fun `Completed resets to Idle`() {
        val completed = PortalSession.Completed(
            closeReason = CloseReason.PORTAL_COMPLETED,
            openedUtc = opened,
            closedUtc = closed,
            portalUrl = portalUrl,
        )
        val idle = manager.reset(completed)
        assertTrue(idle is PortalSession.Idle)
    }

    // ── Invalid transitions rejected ────────────────────────────────────────

    @Test
    fun `invalid Idle to Detected is rejected`() {
        val before = manager.rejectedTransitions.get()
        val result = manager.portalDetected(PortalSession.Idle, portalUrl)
        assertTrue(result is PortalSession.Idle)
        assertEquals(before + 1, manager.rejectedTransitions.get())
    }

    @Test
    fun `invalid Monitoring to Active without Detected is rejected`() {
        val before = manager.rejectedTransitions.get()
        val result = manager.openPortal(PortalSession.Monitoring, opened)
        assertTrue(result is PortalSession.Monitoring)
        assertEquals(before + 1, manager.rejectedTransitions.get())
    }

    @Test
    fun `invalid Active to Active via startMonitoring is rejected`() {
        val active = PortalSession.Active(portalUrl, opened)
        val before = manager.rejectedTransitions.get()
        val result = manager.startMonitoring(active)
        assertTrue(result is PortalSession.Active)
        assertEquals(before + 1, manager.rejectedTransitions.get())
    }

    @Test
    fun `recordBlockedNavigation on Idle is rejected`() {
        val before = manager.rejectedTransitions.get()
        val result = manager.recordBlockedNavigation(PortalSession.Idle)
        assertTrue(result is PortalSession.Idle)
        assertEquals(before + 1, manager.rejectedTransitions.get())
    }

    @Test
    fun `timeout on non-Active is rejected`() {
        val before = manager.rejectedTransitions.get()
        val result = manager.timeout(PortalSession.Monitoring, closed)
        assertTrue(result is PortalSession.Monitoring)
        assertEquals(before + 1, manager.rejectedTransitions.get())
    }

    @Test
    fun `CloseReason schema values match audit log enum`() {
        assertEquals("portal_completed", CloseReason.PORTAL_COMPLETED.schemaValue)
        assertEquals("user_dismissed", CloseReason.USER_DISMISSED.schemaValue)
        assertEquals("timeout", CloseReason.TIMEOUT.schemaValue)
        assertEquals("error", CloseReason.ERROR.schemaValue)
        assertEquals("aborted_pre_active", CloseReason.ABORTED_PRE_ACTIVE.schemaValue)
    }

    // ── Immutability proof: copy() returns a NEW Active, original unchanged ──

    @Test
    fun `Active copy is a new instance, original unchanged`() {
        val active = PortalSession.Active(portalUrl, opened, 0, 0)
        val updated = manager.recordBlockedNavigation(active)
        assertNotEquals(active, updated)
        assertEquals(0, active.blockedNavigationAttempts)
        assertEquals(1, (updated as PortalSession.Active).blockedNavigationAttempts)
    }
}
