package com.ventouxlabs.gatepath.ui

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.VpnService
import android.provider.Settings
import com.ventouxlabs.gatepath.network.VpnKind

/** Finds the VPN app to send the user to. Known packages first, then any installed VpnService. */
object VpnAppLauncher {
    private val KNOWN = mapOf(
        VpnKind.TAILSCALE to listOf("com.tailscale.ipn"),
        VpnKind.TORGUARD to listOf("net.torguard.openvpn.client"),
    )

    fun resolve(context: Context, kind: VpnKind): Pair<String?, Intent> {
        val pm = context.packageManager
        val candidates = KNOWN[kind].orEmpty() + installedVpnPackages(pm)
        for (pkg in candidates) {
            val launch = pm.getLaunchIntentForPackage(pkg) ?: continue
            val label = runCatching { pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString() }.getOrNull()
            return label to launch
        }
        return null to Intent(Settings.ACTION_VPN_SETTINGS)
    }

    @Suppress("DEPRECATION")
    private fun installedVpnPackages(pm: PackageManager): List<String> =
        pm.queryIntentServices(Intent(VpnService.SERVICE_INTERFACE), PackageManager.GET_META_DATA)
            .map { it.serviceInfo.packageName }
            .distinct()
}
