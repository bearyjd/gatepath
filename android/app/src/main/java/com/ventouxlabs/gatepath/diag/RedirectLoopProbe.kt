package com.ventouxlabs.gatepath.diag

import java.net.URL

/** Redirect hops to follow before concluding "long but not looping". */
private const val MAX_HOPS = 5

/**
 * Follows the captive redirect chain from [ProbeContext.probeUrl] via
 * [ProbeContext.httpFetch] (one no-follow GET per hop) and reports
 * [DiagnosticReport.PortalRedirectLoop] when a URL repeats — a looping
 * gateway leaves the user staring at a spinner with no page to sign in on.
 *
 * A chain that terminates (204, page, error mid-chain) or simply runs past
 * [MAX_HOPS] without repeating is not a loop: Healthy. Only a failure of the
 * very first fetch is Inconclusive — mid-chain errors mean the gateway is
 * serving *something*, which other probes judge better.
 */
class RedirectLoopProbe : DiagnosticProbe {
    override val name = "redirect_loop"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        val visited = mutableListOf(ctx.probeUrl)
        var current = ctx.probeUrl
        repeat(MAX_HOPS) { hop ->
            val result = ctx.httpFetch(current, null)
            if (result.error != null) {
                return if (hop == 0) {
                    DiagnosticReport.Inconclusive(listOf("redirect_loop: ${result.error}"))
                } else {
                    DiagnosticReport.Healthy
                }
            }
            val status = result.statusCode ?: return DiagnosticReport.Healthy
            if (status !in 300..399) return DiagnosticReport.Healthy
            val location = result.locationHeader ?: return DiagnosticReport.Healthy
            val next = resolve(current, location)
            visited.add(next)
            if (visited.dropLast(1).contains(next)) {
                return DiagnosticReport.PortalRedirectLoop(chain = visited.toList())
            }
            current = next
        }
        return DiagnosticReport.Healthy
    }

    private fun resolve(base: String, location: String): String =
        runCatching { URL(URL(base), location).toString() }.getOrDefault(location)
}
