package com.ventouxlabs.gatepath.network

/**
 * Which VPN client covers this process, coarse enough to pick a sentence and a
 * launch intent. Derived from [VpnDetector] interface descriptors
 * ("<iface> (<mode>)"). Pure Kotlin so the classifier is JVM-testable.
 */
enum class VpnKind {
    TAILSCALE, TORGUARD, OTHER, NONE;

    companion object {
        fun fromInterfaces(interfaces: List<String>): VpnKind {
            val names = interfaces.map { it.substringBefore(' ').lowercase() }
            return when {
                names.any { it.startsWith("tailscale") } -> TAILSCALE
                names.any { it.startsWith("torguard") } -> TORGUARD
                names.isNotEmpty() -> OTHER
                else -> NONE
            }
        }
    }
}
