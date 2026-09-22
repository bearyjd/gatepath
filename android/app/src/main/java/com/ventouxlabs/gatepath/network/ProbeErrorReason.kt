package com.ventouxlabs.gatepath.network

import android.system.ErrnoException
import android.system.OsConstants

/**
 * Why a captive-portal probe failed, structured instead of parsed from a
 * rendered message string.
 *
 * [ConfinementState.classify] used to decide [ConfinementState.Tunnelled] vs.
 * [ConfinementState.Blocked] by substring-matching "EPERM"/"EACCES" against
 * [ProbeResult.Error.message]. That message is whatever `Throwable.message`
 * (or OEM libcore) happened to render, so any wording drift silently
 * misclassified a genuinely tunnelled probe as [ConfinementState.Unknown] —
 * the one state that offers "try signing in anyway" to a user who cannot
 * actually reach the gateway. [probeErrorReason] walks the exception's cause
 * chain for the real errno instead.
 */
enum class ProbeErrorReason {
    /** Bind refused by netd — a secure VPN covers this UID (errno EPERM). */
    PERMISSION_DENIED,

    /** Bind refused by an always-on lockdown PROHIBIT rule (errno EACCES). */
    ACCESS_BLOCKED,

    /** TCP RST from the peer (errno ECONNREFUSED). */
    CONNECTION_REFUSED,

    /** No route to host (errno ENETUNREACH). */
    UNREACHABLE,

    /** Connect or read timed out (errno ETIMEDOUT, or [java.net.SocketTimeoutException]). */
    TIMEOUT,

    /** Anything else, including a failure this device's platform cannot classify further. */
    OTHER,
}

/**
 * Bound on how many links of a `Throwable` cause chain [probeErrorReason]
 * follows before giving up. A chain this long is already pathological; the
 * bound exists to guarantee termination against a self-referential chain,
 * not because real Android exceptions nest this deep.
 */
private const val MAX_CAUSE_CHAIN_DEPTH = 16

/**
 * Derive a [ProbeErrorReason] from a probe failure.
 *
 * Prefers the typed path: an [ErrnoException] anywhere in [ex]'s cause chain
 * (bounded, and stopping the moment a node repeats so a self-referential
 * chain still terminates) wins over everything else, because it is the
 * actual kernel errno rather than a rendered string. [java.net.SocketTimeoutException]
 * is checked next — it has no errno of its own but unambiguously means
 * [ProbeErrorReason.TIMEOUT]. Only when neither is present does this fall
 * back to the historical substring match on [Throwable.message], documented
 * here as a FALLBACK so a platform that never attaches [ErrnoException]
 * classifies no worse than before this type existed.
 */
internal fun probeErrorReason(ex: Throwable): ProbeErrorReason {
    var current: Throwable? = ex
    val seen = HashSet<Throwable>()
    var depth = 0
    while (current != null && depth < MAX_CAUSE_CHAIN_DEPTH && seen.add(current)) {
        if (current is ErrnoException) {
            return current.errno.toProbeErrorReason() ?: ProbeErrorReason.OTHER
        }
        if (current is java.net.SocketTimeoutException) {
            return ProbeErrorReason.TIMEOUT
        }
        current = current.cause
        depth++
    }
    // FALLBACK: no typed ErrnoException/SocketTimeoutException anywhere in the
    // chain. Match the rendered message the way classify() always has, so a
    // platform that doesn't attach ErrnoException is no worse off.
    val message = ex.message ?: return ProbeErrorReason.OTHER
    return when {
        message.contains("EPERM") -> ProbeErrorReason.PERMISSION_DENIED
        message.contains("EACCES") -> ProbeErrorReason.ACCESS_BLOCKED
        else -> ProbeErrorReason.OTHER
    }
}

private fun Int.toProbeErrorReason(): ProbeErrorReason? = when (this) {
    OsConstants.EPERM -> ProbeErrorReason.PERMISSION_DENIED
    OsConstants.EACCES -> ProbeErrorReason.ACCESS_BLOCKED
    OsConstants.ECONNREFUSED -> ProbeErrorReason.CONNECTION_REFUSED
    OsConstants.ENETUNREACH -> ProbeErrorReason.UNREACHABLE
    OsConstants.ETIMEDOUT -> ProbeErrorReason.TIMEOUT
    else -> null
}
