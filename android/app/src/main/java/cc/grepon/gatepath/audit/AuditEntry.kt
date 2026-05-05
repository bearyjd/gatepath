package cc.grepon.gatepath.audit

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Immutable data class representing one audit log entry.
 * Schema version 1 — must match docs/AUDIT_LOG_SCHEMA.md exactly.
 * Field names use @SerialName to match the JSON snake_case schema.
 */
@Serializable
data class AuditEntry(
    @SerialName("schema_version") val schemaVersion: Int = 1,
    @SerialName("timestamp_utc") val timestampUtc: String,
    @SerialName("platform") val platform: String = "android",
    @SerialName("ssid") val ssid: String?,
    @SerialName("gateway_ip") val gatewayIp: String?,
    @SerialName("portal_domain") val portalDomain: String,
    @SerialName("vpn_interfaces_detected") val vpnInterfacesDetected: List<String>,
    @SerialName("vpn_warning_shown") val vpnWarningShown: Boolean,
    @SerialName("session_opened_utc") val sessionOpenedUtc: String,
    @SerialName("session_closed_utc") val sessionClosedUtc: String?,
    @SerialName("close_reason") val closeReason: String,
    @SerialName("duration_seconds") val durationSeconds: Int,
    @SerialName("blocked_navigation_attempts") val blockedNavigationAttempts: Int,
    @SerialName("blocked_resource_requests") val blockedResourceRequests: Int,
)
