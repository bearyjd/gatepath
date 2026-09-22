package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.LinuxErrno
import com.ventouxlabs.gatepath.network.ProbeErrorReason
import com.ventouxlabs.gatepath.network.probeErrorReason
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException

/**
 * [probeErrorReason] is the typed-errno path [com.ventouxlabs.gatepath.network.classify]
 * relies on to tell a tunnelled bind from a genuinely inconclusive probe —
 * see the KDoc on `ProbeErrorReason.kt` for why matching only message text
 * was unsafe.
 *
 * These tests never construct `android.system.ErrnoException`. Under Gradle
 * the unit tests link the `android.jar` stubs, whose constructors are no-ops,
 * so its `errno` field always reads 0 there — the hand-rolled JVM runner's
 * stub would carry the real value and hide that (PR #170's Gradle runs).
 * The derivation therefore takes an errno-reader function; production passes
 * the `ErrnoException` reader, and these tests pass one for [Errno].
 */
class ProbeErrorReasonTest {

    /** A throwable carrying a kernel errno, standing in for ErrnoException. */
    private class Errno(val errno: Int) : IOException("errno $errno")

    private val errnoOf: (Throwable) -> Int? = { (it as? Errno)?.errno }

    private fun reason(ex: Throwable): ProbeErrorReason = probeErrorReason(ex, errnoOf)

    /** A cause chain that points to itself, to prove the walk terminates. */
    private class SelfCyclicThrowable : Throwable() {
        override val cause: Throwable?
            get() = this
    }

    @Test
    fun `ConnectException wrapping errno EPERM is PERMISSION_DENIED regardless of the outer message`() {
        val outer = ConnectException("some OEM-specific wording that says nothing about EPERM")
        outer.initCause(Errno(LinuxErrno.EPERM))
        assertEquals(ProbeErrorReason.PERMISSION_DENIED, reason(outer))
    }

    @Test
    fun `errno EACCES nested two levels deep is ACCESS_BLOCKED`() {
        val mid = IOException("wrapped", Errno(LinuxErrno.EACCES))
        val outer = ConnectException("outer")
        outer.initCause(mid)
        assertEquals(ProbeErrorReason.ACCESS_BLOCKED, reason(outer))
    }

    @Test
    fun `errno ECONNREFUSED is CONNECTION_REFUSED`() {
        assertEquals(ProbeErrorReason.CONNECTION_REFUSED, reason(Errno(LinuxErrno.ECONNREFUSED)))
    }

    @Test
    fun `errno ENETUNREACH is UNREACHABLE`() {
        assertEquals(ProbeErrorReason.UNREACHABLE, reason(Errno(LinuxErrno.ENETUNREACH)))
    }

    @Test
    fun `errno ETIMEDOUT is TIMEOUT`() {
        assertEquals(ProbeErrorReason.TIMEOUT, reason(Errno(LinuxErrno.ETIMEDOUT)))
    }

    @Test
    fun `an unmapped errno is OTHER, never the message fallback`() {
        // errno 2 (ENOENT) has no mapping; the typed path still wins over the
        // message, which here would have matched the EPERM fallback.
        val ex = Errno(2)
        val outer = IOException("connect failed: EPERM (Operation not permitted)", ex)
        assertEquals(ProbeErrorReason.OTHER, reason(outer))
    }

    @Test
    fun `SocketTimeoutException with no errno is TIMEOUT`() {
        assertEquals(ProbeErrorReason.TIMEOUT, reason(SocketTimeoutException("timeout")))
    }

    @Test
    fun `an errno beneath a SocketTimeoutException wins over the timeout`() {
        // Chain-wide priority, not per node: the kernel errno is the real
        // answer even when a timeout wrapper sits above it.
        val outer = SocketTimeoutException("timed out")
        outer.initCause(Errno(LinuxErrno.EPERM))
        assertEquals(ProbeErrorReason.PERMISSION_DENIED, reason(outer))
    }

    @Test
    fun `plain IOException with EPERM in the message and no cause falls back to PERMISSION_DENIED`() {
        val ex = IOException("connect failed: EPERM (Operation not permitted)")
        assertEquals(ProbeErrorReason.PERMISSION_DENIED, reason(ex))
    }

    @Test
    fun `plain IOException with EACCES in the message and no cause falls back to ACCESS_BLOCKED`() {
        val ex = IOException("connect failed: EACCES (Permission denied)")
        assertEquals(ProbeErrorReason.ACCESS_BLOCKED, reason(ex))
    }

    @Test
    fun `message with neither token and no cause is OTHER`() {
        assertEquals(ProbeErrorReason.OTHER, reason(IOException("host unreachable")))
    }

    @Test
    fun `an exception with no message and no cause is OTHER`() {
        assertEquals(ProbeErrorReason.OTHER, reason(IOException()))
    }

    @Test
    fun `a self-referential cause chain terminates instead of looping forever`() {
        assertEquals(ProbeErrorReason.OTHER, reason(SelfCyclicThrowable()))
    }

    @Test
    fun `the default errno reader ignores throwables that are not ErrnoException`() {
        // Production reader, no errno anywhere: falls through to the message.
        val ex = IOException("connect failed: EACCES (Permission denied)", Errno(LinuxErrno.EPERM))
        assertEquals(ProbeErrorReason.ACCESS_BLOCKED, probeErrorReason(ex))
    }
}
