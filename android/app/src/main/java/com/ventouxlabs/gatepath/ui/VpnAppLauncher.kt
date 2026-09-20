package com.ventouxlabs.gatepath.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.VpnService
import android.provider.Settings
import com.ventouxlabs.gatepath.network.VpnKind

/**
 * Finds the VPN app to send the user to. Known packages first, then any
 * installed app whose `VpnService` the system would actually bind.
 *
 * Two trust rules, both because the result lands in Gatepath's own security
 * advisory ("Exclude Gatepath in <label>…") and behind an "Open VPN app"
 * button the user is primed to tap:
 *
 * 1. A package discovered by enumeration is a VPN candidate only if its
 *    service is guarded by `BIND_VPN_SERVICE`. Any app can declare the
 *    `android.net.VpnService` intent filter unprivileged — and the
 *    `<queries>` block in the manifest makes every such app visible — but
 *    without that permission the system never binds it as a VPN, so it is
 *    neither named nor launched here. [KNOWN] packages are exempt: they are
 *    the trust anchor, and package names are installer-unique.
 * 2. Only a [KNOWN] vendor's application label is interpolated into the
 *    advisory. For a package discovered by enumeration the label is left
 *    null, and the card falls back to the kind's own vendor name or, for an
 *    unrecognised kind, "your VPN app" (see `ConfinementStateText`), so a
 *    third-party application label — arbitrary text — is never rendered as
 *    if Gatepath had written it.
 * 3. The name and the launch always agree. When the kind has known packages
 *    and none of them is launchable, the fallback is the system VPN settings
 *    rather than some other installed VPN: the card would say "Tailscale"
 *    while the button opened a different vendor. Enumeration is consulted
 *    only for kinds with no known package (`OTHER`), where the card's generic
 *    wording matches whatever verified VPN app is launched.
 */
object VpnAppLauncher {
    private val KNOWN = mapOf(
        VpnKind.TAILSCALE to listOf("com.tailscale.ipn"),
        VpnKind.TORGUARD to listOf("net.torguard.openvpn.client"),
    )

    fun resolve(context: Context, kind: VpnKind): Pair<String?, Intent> {
        val pm = context.packageManager
        val known = KNOWN[kind].orEmpty()
        val candidates = if (known.isNotEmpty()) known else installedVpnPackages(pm)
        for (pkg in candidates) {
            val launch = pm.getLaunchIntentForPackage(pkg) ?: continue
            val label = if (pkg in known) labelOf(pm, pkg) else null
            return label to launch
        }
        return null to Intent(Settings.ACTION_VPN_SETTINGS)
    }

    /** Null for a missing or blank label; `ConfinementStateText` re-checks blank as well. */
    private fun labelOf(pm: PackageManager, pkg: String): String? =
        runCatching { pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString() }
            .getOrNull()
            ?.takeUnless { it.isBlank() }

    @Suppress("DEPRECATION")
    private fun installedVpnPackages(pm: PackageManager): List<String> =
        pm.queryIntentServices(Intent(VpnService.SERVICE_INTERFACE), PackageManager.GET_META_DATA)
            .mapNotNull { it.serviceInfo }
            .filter { it.permission == Manifest.permission.BIND_VPN_SERVICE }
            .map { it.packageName }
            .distinct()
}
