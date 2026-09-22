package com.ventouxlabs.gatepath.network

import android.net.Network

/**
 * The only view of `ConnectivityManager.bindProcessToNetwork` that
 * [ProcessBinding] needs. Kept minimal so it can be faked in JVM tests: the
 * JVM test stub for [android.net.Network] (see `run-jvm-tests.sh`) has no
 * `ConnectivityManager` counterpart, which is why this seam exists at all.
 * The real `ConnectivityManager`-backed implementation is added in stage 2.
 */
interface NetworkBinder {
    /**
     * Binds the process to [network] (or unbinds when null). Returns false
     * when the platform refuses the bind — e.g. `EPERM` under a secure VPN.
     */
    fun bind(network: Network?): Boolean

    /** The network the process is currently bound to, or null. */
    fun current(): Network?
}
