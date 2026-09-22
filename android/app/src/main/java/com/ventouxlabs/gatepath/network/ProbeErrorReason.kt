package com.ventouxlabs.gatepath.network

import android.system.ErrnoException

/**
 * Why a captive-portal probe failed, structured instead of parsed from a
 * rendered message string.
 *
 * [classify] used to decide [ConfinementState.Tunnelled] vs.
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
 * The production errno reader: the kernel errno an [ErrnoException] carries,
 * null for any other throwable. Passed to [probeErrorReason] by default and
 * replaced in unit tests, because `android.jar`'s [ErrnoException] is a stub
 * whose constructor never assigns `errno` (it always reads 0 under Gradle),
 * so no test can construct a meaningful one.
 */
internal fun androidErrno(t: Throwable): Int? = (t as? ErrnoException)?.errno

/**
 * Derive a [ProbeErrorReason] from a probe failure.
 *
 * Prefers the typed path, chain-wide rather than per node: a throwable
 * anywhere in [ex]'s cause chain for which [errnoOf] yields an errno
 * (bounded, and stopping the moment a node repeats so a self-referential
 * chain still terminates) wins over everything else, because it is the
 * actual kernel errno rather than a rendered string — including over a
 * [java.net.SocketTimeoutException] that merely wraps it. A timeout with no
 * errno anywhere beneath it unambiguously means [ProbeErrorReason.TIMEOUT].
 * Only when neither is present does this fall back to the historical
 * substring match on [Throwable.message], documented here as a FALLBACK so a
 * platform that never attaches an errno classifies no worse than before this
 * type existed.
 *
 * The fallback reads text a gateway can influence (a malformed status line
 * surfaces in the exception message), but its only effect is to force
 * PERMISSION_DENIED/ACCESS_BLOCKED, i.e. to *withhold* the sign-in page. It
 * can never manufacture a reason that offers one, so the direction is
 * fail-closed.
 */
internal fun probeErrorReason(
    ex: Throwable,
    errnoOf: (Throwable) -> Int? = ::androidErrno,
): ProbeErrorReason {
    var current: Throwable? = ex
    val seen = HashSet<Throwable>()
    var depth = 0
    var sawTimeout = false
    while (current != null && depth < MAX_CAUSE_CHAIN_DEPTH && seen.add(current)) {
        val errno = errnoOf(current)
        if (errno != null) {
            return errno.toProbeErrorReason() ?: ProbeErrorReason.OTHER
        }
        if (current is java.net.SocketTimeoutException) {
            sawTimeout = true
        }
        current = current.cause
        depth++
    }
    if (sawTimeout) return ProbeErrorReason.TIMEOUT
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

/**
 * The Linux errno values this classification cares about, as compile-time
 * constants. Deliberately NOT `android.system.OsConstants`: those fields are
 * filled in natively at class-init, so under Gradle unit tests (which link
 * the `android.jar` stubs) every one of them reads as 0 and the `when` below
 * would send every errno to its first branch — the four failures on PR #170's
 * first Gradle run. The generic errno numbering is the same on every Android
 * ABI still supported (arm64, arm, x86_64, x86 all use the generic table).
 */
internal object LinuxErrno {
    const val EPERM = 1
    const val EACCES = 13
    const val ENETUNREACH = 101
    const val ETIMEDOUT = 110
    const val ECONNREFUSED = 111
}

private fun Int.toProbeErrorReason(): ProbeErrorReason? = when (this) {
    LinuxErrno.EPERM -> ProbeErrorReason.PERMISSION_DENIED
    LinuxErrno.EACCES -> ProbeErrorReason.ACCESS_BLOCKED
    LinuxErrno.ECONNREFUSED -> ProbeErrorReason.CONNECTION_REFUSED
    LinuxErrno.ENETUNREACH -> ProbeErrorReason.UNREACHABLE
    LinuxErrno.ETIMEDOUT -> ProbeErrorReason.TIMEOUT
    else -> null
}
