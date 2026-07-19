package com.ventouxlabs.gatepath.diag

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.net.URL

/**
 * Cloudflare's DoH JSON endpoint, addressed by IP literal deliberately: the
 * hostname form would itself need bootstrap resolution through the very
 * system resolver this probe suspects of hijacking. 1.1.1.1 is in
 * Cloudflare's certificate SAN, so TLS and the JSON API work identically.
 */
private const val DOH_ENDPOINT = "https://1.1.1.1/dns-query"
private const val DOH_ACCEPT = "application/dns-json"

/** DNS record type 1 = A. We only compare IPv4 answers. */
private const val TYPE_A = 1

/**
 * Compares the system resolver's answer for the connectivity-check host
 * ([ProbeContext.resolveHost]) against a DNS-over-HTTPS lookup
 * ([ProbeContext.httpFetch] on Cloudflare's JSON API via the 1.1.1.1 IP
 * literal, not the hostname — see [DOH_ENDPOINT]). A gateway that answers
 * with its own private address while the true record is public is hijacking
 * DNS beyond the probe endpoints — the aggressive-captive signature that also
 * breaks HTTPS after sign-in.
 *
 * Verdict policy (conservative — false negatives over false alarms):
 * system resolve fails → Inconclusive; DoH unreachable/unparseable → Healthy
 * (DoH being blocked pre-login is normal captivity, not hijack evidence);
 * both answer → DnsHijack only when EVERY system answer is private/loopback
 * and DoH returned at least one public address.
 */
class DnsHijackProbe : DiagnosticProbe {
    override val name = "dns_hijack"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        val host = runCatching { URL(ctx.probeUrl).host }.getOrNull()
            ?: return DiagnosticReport.Inconclusive(listOf("dns_hijack: unparseable probe url"))

        val systemAnswers = ctx.resolveHost(host)
        if (systemAnswers.isEmpty()) {
            return DiagnosticReport.Inconclusive(listOf("dns_hijack: system resolver returned no answers for $host"))
        }

        val doh = ctx.httpFetch("$DOH_ENDPOINT?name=$host&type=A", DOH_ACCEPT)
        val dohAnswers = doh.body?.let(::parseDohAddresses).orEmpty()
        val dohPublic = dohAnswers.filterNot(::isPrivateOrLoopback)
        if (doh.error != null || dohPublic.isEmpty()) return DiagnosticReport.Healthy

        val allSystemPrivate = systemAnswers.all(::isPrivateOrLoopback)
        return if (allSystemPrivate) {
            DiagnosticReport.DnsHijack(
                hostProbed = host,
                systemAnswer = systemAnswers.first(),
                doHAnswer = dohPublic.first(),
            )
        } else {
            DiagnosticReport.Healthy
        }
    }
}

/** Extracts A-record `data` fields from a DoH JSON body; empty on any parse problem. */
internal fun parseDohAddresses(body: String): List<String> = runCatching {
    Json.parseToJsonElement(body).jsonObject["Answer"]?.jsonArray.orEmpty()
        .mapNotNull { answer ->
            val obj = answer.jsonObject
            val type = obj["type"]?.jsonPrimitive?.content?.toIntOrNull()
            if (type == TYPE_A) obj["data"]?.jsonPrimitive?.content else null
        }
}.getOrDefault(emptyList())

/** RFC1918 / loopback / link-local — the address ranges captive gateways answer with. */
internal fun isPrivateOrLoopback(address: String): Boolean {
    if (address.startsWith("10.") || address.startsWith("192.168.") ||
        address.startsWith("127.") || address.startsWith("169.254.")
    ) {
        return true
    }
    if (address.startsWith("172.")) {
        val second = address.split(".").getOrNull(1)?.toIntOrNull() ?: return false
        return second in 16..31
    }
    return false
}
