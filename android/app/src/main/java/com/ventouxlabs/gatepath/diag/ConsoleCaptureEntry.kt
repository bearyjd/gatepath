package com.ventouxlabs.gatepath.diag

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * One WebView console.log/warn/error line captured during a portal session.
 * Flushed to `files/webview-console.jsonl` (see [ConsoleCaptureFile]) on
 * session end so Share Diagnostics can include it later. Deliberately
 * separate from audit.jsonl: this file is Android-only and NOT part of the
 * desktop schema-parity contract (see docs/AUDIT_LOG_SCHEMA.md).
 */
@Serializable
data class ConsoleCaptureEntry(
    @SerialName("level") val level: String,
    @SerialName("source_host") val sourceHost: String,
    @SerialName("line_number") val lineNumber: Int,
    @SerialName("message") val message: String,
    @SerialName("offset_ms") val offsetMs: Long,
)
