package com.ventouxlabs.gatepath

import android.net.Network
import com.ventouxlabs.gatepath.network.NetworkBinder
import com.ventouxlabs.gatepath.network.ProcessBinding
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertNotNull
import org.junit.Test

/**
 * Pure-JVM tests for [ProcessBinding] against a fake [NetworkBinder].
 *
 * android.net.Network's JVM stub (see `run-jvm-tests.sh`) has no
 * equals/hashCode, so distinct `Network()` instances are distinct networks
 * by reference — exactly what these tests need to tell owners and borrows
 * apart.
 */
class ProcessBindingTest {

    /** Records every bind() call in order; can be told to refuse a network. */
    private class FakeBinder : NetworkBinder {
        val calls = mutableListOf<Network?>()
        private var bound: Network? = null
        private var refused: Network? = null

        fun refuse(network: Network) {
            refused = network
        }

        override fun bind(network: Network?): Boolean {
            calls.add(network)
            if (network != null && network === refused) return false
            bound = network
            return true
        }

        override fun current(): Network? = bound
    }

    @Test
    fun `acquire binds, release of the only lease binds null`() {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val net = Network()

        val lease = binding.acquire(net)
        assertNotNull(lease)
        assertEquals(net, binding.current())

        binding.release(requireNotNull(lease))
        assertNull(binding.current())
        assertEquals(listOf(net, null), binder.calls)
    }

    @Test
    fun `two owners - B on top, releases unwind B then A then null`() {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val netA = Network()
        val netB = Network()

        val leaseA = requireNotNull(binding.acquire(netA))
        val leaseB = requireNotNull(binding.acquire(netB))
        assertEquals(netB, binding.current())

        binding.release(leaseB)
        assertEquals(netA, binding.current())

        binding.release(leaseA)
        assertNull(binding.current())
    }

    @Test
    fun `release out of order leaves the remaining owner bound`() {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val netA = Network()
        val netB = Network()

        val leaseA = requireNotNull(binding.acquire(netA))
        val leaseB = requireNotNull(binding.acquire(netB))

        binding.release(leaseA)
        assertEquals(netB, binding.current())

        binding.release(leaseB)
        assertNull(binding.current())
    }

    @Test
    fun `same network twice keeps it bound until both leases release`() {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val net = Network()

        val lease1 = requireNotNull(binding.acquire(net))
        val lease2 = requireNotNull(binding.acquire(net))

        binding.release(lease1)
        assertEquals(net, binding.current())

        binding.release(lease2)
        assertNull(binding.current())
    }

    @Test
    fun `refused bind returns null and leaves the slot untouched`() {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val net = Network()
        binder.refuse(net)

        val lease = binding.acquire(net)
        assertNull(lease)
        assertNull(binding.current())
    }

    @Test
    fun `borrow restores to the current owner, not the saved value`() = runTest {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val netA = Network()
        val netB = Network()
        val probe = Network()

        val leaseA = requireNotNull(binding.acquire(netA))

        binding.borrow(probe) {
            assertEquals(probe, binding.current())
            binding.release(leaseA)
            binding.acquire(netB)
        }

        assertEquals(netB, binding.current())
    }

    @Test
    fun `borrow with no owner ends at null even if an owner acquired and released during it`() = runTest {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val probe = Network()
        val net = Network()

        binding.borrow(probe) {
            val lease = requireNotNull(binding.acquire(net))
            assertEquals(probe, binding.current())
            binding.release(lease)
        }

        assertNull(binding.current())
    }

    @Test
    fun `two concurrent borrows serialise`() = runTest {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val netA = Network()
        val netB = Network()
        val observed = mutableListOf<Network?>()

        val jobA = launch {
            binding.borrow(netA) {
                delay(10)
                observed.add(binding.current())
            }
        }
        val jobB = launch {
            binding.borrow(netB) {
                delay(10)
                observed.add(binding.current())
            }
        }
        jobA.join()
        jobB.join()

        assertEquals(setOf(netA, netB), observed.toSet())

        val calls = binder.calls
        assertEquals(4, calls.size)
        assertNotNull(calls[0])
        assertNull(calls[1])
        assertNotNull(calls[2])
        assertNull(calls[3])
        assertEquals(setOf(netA, netB), setOf(calls[0], calls[2]))
    }

    @Test
    fun `releaseAll during a borrow ends at null immediately, not only after the borrow finishes`() = runTest {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val net = Network()
        val probe = Network()

        requireNotNull(binding.acquire(net))

        binding.borrow(probe) {
            assertEquals(probe, binding.current())
            binding.releaseAll()
            // releaseAll is the leak defense for whole-app-background /
            // onTerminate; it must not wait for the borrow to finish, so the
            // slot is already null mid-block, not just after the block returns.
            assertNull(binding.current())
        }

        assertNull(binding.current())
    }

    @Test
    fun `acquire during a borrow always succeeds even for a network the binder will refuse`() = runTest {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val probe = Network()
        val badNet = Network()
        binder.refuse(badNet)

        var leaseDuringBorrow: ProcessBinding.Lease? = null
        binding.borrow(probe) {
            // acquire cannot test the real bind without disturbing the
            // borrow's pinned network (see the class KDoc), so it always
            // succeeds during a borrow -- even for a network the binder will
            // actually refuse.
            leaseDuringBorrow = binding.acquire(badNet)
        }

        assertNotNull(leaseDuringBorrow)
        // The deferred bind (applied when the borrow ended) was refused, so
        // the binder's own state was never updated to badNet: current()
        // still reflects the last successful bind (the probe network), not
        // the lease the stack claims to hold. This is the documented gap in
        // the "a refused bind never claims the slot" guarantee during a
        // borrow window.
        assertEquals(probe, binding.current())
    }

    @Test
    fun `release of a stale lease after releaseAll is a no-op`() {
        val binder = FakeBinder()
        val binding = ProcessBinding(binder)
        val netA = Network()
        val netB = Network()

        val leaseA = requireNotNull(binding.acquire(netA))
        binding.releaseAll()
        assertNull(binding.current())

        val leaseB = requireNotNull(binding.acquire(netB))
        binding.release(leaseA)
        assertEquals(netB, binding.current())

        binding.release(leaseB)
        assertNull(binding.current())
    }
}
