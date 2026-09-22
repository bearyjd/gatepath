package com.ventouxlabs.gatepath.network

import android.net.ConnectivityManager
import android.net.Network

/**
 * [NetworkBinder] backed by the real [ConnectivityManager]. The only
 * production implementation of the seam [ProcessBinding] depends on;
 * [ProcessBinding]'s own tests use a fake `NetworkBinder` instead (see
 * `ProcessBindingTest`).
 *
 * Not part of `run-jvm-tests.sh`'s SDK-free subset — both
 * [ConnectivityManager] and [Network] here are the real Android SDK types,
 * not the JVM stubs the runner generates.
 */
class AndroidNetworkBinder(
    private val connectivityManager: ConnectivityManager,
) : NetworkBinder<Network> {

    override fun bind(network: Network?): Boolean =
        connectivityManager.bindProcessToNetwork(network)

    override fun current(): Network? = connectivityManager.boundNetworkForProcess
}

/**
 * [ProcessBinding] parameterised for the real Android [Network] type — the
 * only production instantiation. Kept here (not in `ProcessBinding.kt`,
 * which stays SDK-free for `run-jvm-tests.sh`) since this file is already
 * Android-only.
 */
typealias AndroidProcessBinding = ProcessBinding<Network>
