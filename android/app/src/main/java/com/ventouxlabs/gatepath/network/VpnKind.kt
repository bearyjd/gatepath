package com.ventouxlabs.gatepath.network

/**
 * Which VPN client covers this process, coarse enough to pick a sentence and a
 * launch intent. Derived from [VpnDetector] interface descriptors
 * ("<iface> (<mode>)"). Pure Kotlin so the classifier is JVM-testable.
 *
 * Every non-null [interfacePrefix] must also appear in
 * [VpnHeuristics.VPN_PREFIXES] — the authoritative interface-prefix list —
 * and `VpnKindTest` guards that this stays true.
 */
enum class VpnKind(val interfacePrefix: String?) {
    TAILSCALE("tailscale"),
    TORGUARD("torguard"),
    OTHER(null),
    NONE(null);

    companion object {
        fun fromInterfaces(interfaces: List<String>): VpnKind {
            val names = interfaces.map { it.substringBefore(' ').lowercase() }
            val vendorPrefixes = entries.mapNotNull { kind -> kind.interfacePrefix?.let { kind to it } }
            return vendorPrefixes.firstOrNull { (_, prefix) -> names.any { it.startsWith(prefix) } }?.first
                ?: if (names.isNotEmpty()) OTHER else NONE
        }
    }
}
