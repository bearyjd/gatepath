package com.ventouxlabs.gatepath

import android.system.ErrnoException
import android.system.OsConstants
import com.ventouxlabs.gatepath.network.ProbeErrorReason
import com.ventouxlabs.gatepath.network.probeErrorReason
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException

/**
 * [probeErrorReason] is the typed-errno path [ConfinementState.classify]
 * relies on to tell a tunnelled bind from a genuinely inconclusive probe —
 * see the KDoc on `ProbeErrorReason.kt` for why matching only message text
 * was unsafe.
 */
class ProbeErrorReasonTest {

    /** A cause chain that points to itself, to prove the walk terminates. */
    private class SelfCyclicThrowable : Throwable() {
        override val cause: Throwable?
            get() = this
    }

    @Test
    fun `ConnectException wrapping ErrnoException EPERM is PERMISSION_DENIED regardless of the outer message`() {
        val errno = ErrnoException("connect", OsConstants.EPERM)
        val outer = ConnectException("some OEM-specific wording that says nothing about EPERM")
        outer.initCause(errno)
        assertEquals(ProbeErrorReason.PERMISSION_DENIED, probeErrorReason(outer))
    }

    @Test
    fun `ErrnoException EACCES nested two levels deep is ACCESS_BLOCKED`() {
        val errno = ErrnoException("connect", OsConstants.EACCES)
        val mid = IOException("wrapped", errno)
        val outer = ConnectException("outer")
        outer.initCause(mid)
        assertEquals(ProbeErrorReason.ACCESS_BLOCKED, probeErrorReason(outer))
    }

    @Test
    fun `ErrnoException ECONNREFUSED is CONNECTION_REFUSED`() {
        val errno = ErrnoException("connect", OsConstants.ECONNREFUSED)
        assertEquals(ProbeErrorReason.CONNECTION_REFUSED, probeErrorReason(errno))
    }

    @Test
    fun `ErrnoException ENETUNREACH is UNREACHABLE`() {
        val errno = ErrnoException("connect", OsConstants.ENETUNREACH)
        assertEquals(ProbeErrorReason.UNREACHABLE, probeErrorReason(errno))
    }

    @Test
    fun `ErrnoException ETIMEDOUT is TIMEOUT`() {
        val errno = ErrnoException("connect", OsConstants.ETIMEDOUT)
        assertEquals(ProbeErrorReason.TIMEOUT, probeErrorReason(errno))
    }

    @Test
    fun `SocketTimeoutException with no ErrnoException is TIMEOUT`() {
        assertEquals(ProbeErrorReason.TIMEOUT, probeErrorReason(SocketTimeoutException("timeout")))
    }

    @Test
    fun `plain IOException with EPERM in the message and no cause falls back to PERMISSION_DENIED`() {
        val ex = IOException("connect failed: EPERM (Operation not permitted)")
        assertEquals(ProbeErrorReason.PERMISSION_DENIED, probeErrorReason(ex))
    }

    @Test
    fun `plain IOException with EACCES in the message and no cause falls back to ACCESS_BLOCKED`() {
        val ex = IOException("connect failed: EACCES (Permission denied)")
        assertEquals(ProbeErrorReason.ACCESS_BLOCKED, probeErrorReason(ex))
    }

    @Test
    fun `message with neither token and no cause is OTHER`() {
        assertEquals(ProbeErrorReason.OTHER, probeErrorReason(IOException("host unreachable")))
    }

    @Test
    fun `an exception with no message and no cause is OTHER`() {
        assertEquals(ProbeErrorReason.OTHER, probeErrorReason(IOException()))
    }

    @Test
    fun `a self-referential cause chain terminates instead of looping forever`() {
        assertEquals(ProbeErrorReason.OTHER, probeErrorReason(SelfCyclicThrowable()))
    }
}
