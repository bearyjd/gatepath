package cc.grepon.gatepath

import cc.grepon.gatepath.session.CloseReason
import cc.grepon.gatepath.session.PortalSession
import cc.grepon.gatepath.session.PortalSessionManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Pure-JVM tests for [PortalSessionManager] state transitions. No Android SDK required.
 */
class SessionStateTest {

    private lateinit var manager: PortalSessionManager

    @Before
    fun setUp() {
        manager = PortalSessionManager()
    }

    // ── Happy-path transitions ──────────────────────────────────────────────

    @Test
    fun `Idle to Monitoring`() {
        val next = manager.startMonitoring(PortalSession.Idle)
        assertTrue(next is PortalSession.Monitoring)
    }

    @Test
    fun `Monitoring to Detected`() {
        val next = manager.portalDetected(PortalSession.Monitoring, "http://portal.example.com/")
        assertTrue(next is PortalSession.Detected)
        assertEquals("http://portal.example.com/", (next as PortalSession.Detected).portalUrl)
    }

    @Test
    fun `Detected to Active`() {
        val detected = PortalSession.Detected("http://portal.example.com/")
        val next = manager.openPortal(detected)
        assertTrue(next is PortalSession.Active)
        assertEquals("http://portal.example.com/", (next as PortalSession.Active).portalUrl)
    }

    @Test
    fun `Active records blocked navigations immutably`() {
        val active = PortalSession.Active("http://portal.example.com/")
        val after1 = manager.recordBlockedNavigation(active)
        val after2 = manager.recordBlockedNavigation(after1)
        assertEquals(0, active.blockedNavigationAttempts) // original unchanged
        assertEquals(1, (after1 as PortalSession.Active).blockedNavigationAttempts)
        assertEquals(2, (after2 as PortalSession.Active).blockedNavigationAttempts)
    }

    @Test
    fun `Active records blocked resources immutably`() {
        val active = PortalSession.Active("http://portal.example.com/")
        val after = manager.recordBlockedResource(active)
        assertEquals(0, active.blockedResourceRequests) // original unchanged
        assertEquals(1, (after as PortalSession.Active).blockedResourceRequests)
    }

    @Test
    fun `Active to Completed via completePortal`() {
        val active = PortalSession.Active("http://portal.example.com/", 2, 5)
        val next = manager.completePortal(active)
        assertTrue(next is PortalSession.Completed)
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.PORTAL_COMPLETED, completed.closeReason)
        assertEquals(2, completed.blockedNavigationAttempts)
        assertEquals(5, completed.blockedResourceRequests)
    }

    @Test
    fun `Active to Completed via dismiss`() {
        val active = PortalSession.Active("http://portal.example.com/", 1, 3)
        val next = manager.dismiss(active)
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.USER_DISMISSED, completed.closeReason)
        assertEquals(1, completed.blockedNavigationAttempts)
        assertEquals(3, completed.blockedResourceRequests)
    }

    @Test
    fun `Active to Completed via timeout`() {
        val active = PortalSession.Active("http://portal.example.com/", 0, 7)
        val next = manager.timeout(active)
        val completed = next as PortalSession.Completed
        assertEquals(CloseReason.TIMEOUT, completed.closeReason)
        assertEquals(7, completed.blockedResourceRequests)
    }

    @Test
    fun `Any to Error then reset to Idle`() {
        val errored = manager.error(PortalSession.Monitoring, "connection refused")
        assertTrue(errored is PortalSession.Error)
        assertEquals("connection refused", (errored as PortalSession.Error).message)

        val idle = manager.reset(errored)
        assertTrue(idle is PortalSession.Idle)
    }

    @Test
    fun `Completed resets to Idle`() {
        val completed = PortalSession.Completed(CloseReason.PORTAL_COMPLETED)
        val idle = manager.reset(completed)
        assertTrue(idle is PortalSession.Idle)
    }

    // ── Invalid transitions rejected ────────────────────────────────────────

    @Test
    fun `invalid Idle to Detected is rejected`() {
        val before = manager.rejectedTransitions.get()
        val result = manager.portalDetected(PortalSession.Idle, "http://x.com")
        assertTrue(result is PortalSession.Idle)
        assertEquals(before + 1, manager.rejectedTransitions.get())
    }

    @Test
    fun `invalid Monitoring to Active without Detected is rejected`() {
        val before = manager.rejectedTransitions.get()
        val result = manager.openPortal(PortalSession.Monitoring)
        assertTrue(result is PortalSession.Monitoring)
        assertEquals(before + 1, manager.rejectedTransitions.get())
    }

    @Test
    fun `invalid Active to Active via startMonitoring is rejected`() {
        val active = PortalSession.Active("http://portal.example.com/")
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
        val result = manager.timeout(PortalSession.Monitoring)
        assertTrue(result is PortalSession.Monitoring)
        assertEquals(before + 1, manager.rejectedTransitions.get())
    }

    @Test
    fun `CloseReason schema values match audit log enum`() {
        assertEquals("portal_completed", CloseReason.PORTAL_COMPLETED.schemaValue)
        assertEquals("user_dismissed", CloseReason.USER_DISMISSED.schemaValue)
        assertEquals("timeout", CloseReason.TIMEOUT.schemaValue)
        assertEquals("error", CloseReason.ERROR.schemaValue)
    }
}
