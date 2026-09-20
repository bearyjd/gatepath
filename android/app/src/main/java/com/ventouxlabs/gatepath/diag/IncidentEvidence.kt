package com.ventouxlabs.gatepath.diag

import com.ventouxlabs.gatepath.network.PortalProbeCapture
import com.ventouxlabs.gatepath.network.ProbePath
import com.ventouxlabs.gatepath.network.VpnKind

/**
 * One record per captive incident, produced in every [ConfinementState], not
 * only when a session opens. This is the debugging artefact; the audit log
 * stays a session log. Free-text fields ([bindError], [fallbackError]) are
 * rendered through the bundle's redaction pass; everything else is an enum,
 * number, boolean, date or fingerprint. `IncidentEvidenceTest` guards the set.
 */
data class IncidentEvidence(
    val confinement: String,
    val probePath: ProbePath,
    val probeCapture: PortalProbeCapture?,
    /** IP literals the Wi-Fi network's resolver returned for the probe host; empty = failed/not run. */
    val resolverWifi: List<String>,
    /** IP literals DoH (1.1.1.1) returned; empty = failed/not run/declined. */
    val resolverDoh: List<String>,
    val certSummary: CertSummary?,
    val vpnKind: VpnKind,
    val vpnInterfaces: List<String>,
    val privateDnsStrict: Boolean,
    val bindError: String?,
    val fallbackError: String?,
)
