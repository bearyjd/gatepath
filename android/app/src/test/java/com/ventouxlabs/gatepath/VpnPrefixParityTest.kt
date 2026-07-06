package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.network.VpnHeuristics
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Cross-platform parity guard for the VPN-interface prefix list.
 *
 * `docs/SECURITY_MODEL.md` declares its prefix list "the source of truth —
 * both platforms must match it," and splits it into three categories:
 * Common (Android + desktop), Desktop-only, and Android-only. This test fails
 * the build if either platform's implementation drifts from the documented
 * sets — Android [VpnHeuristics.VPN_PREFIXES] vs Common + Android-only, and the
 * desktop `_VPN_PREFIXES` vs Common + Desktop-only — so a one-sided edit (which
 * would silently open a VPN-detection blind spot on one platform) cannot land.
 */
class VpnPrefixParityTest {

    @Test
    fun `android prefixes match the documented common plus android-only sets`() {
        val doc = documentedPrefixes()
        assertEquals(
            "VpnHeuristics.VPN_PREFIXES (Android) has drifted from " +
                "docs/SECURITY_MODEL.md (Common + Android-only). Update both together.",
            doc.common + doc.androidOnly,
            VpnHeuristics.VPN_PREFIXES.toSet(),
        )
    }

    @Test
    fun `desktop prefixes match the documented common plus desktop-only sets`() {
        val doc = documentedPrefixes()
        assertEquals(
            "_VPN_PREFIXES in desktop/gatepath/vpn_detector.py has drifted from " +
                "docs/SECURITY_MODEL.md (Common + Desktop-only). Update both together.",
            doc.common + doc.desktopOnly,
            desktopDetectorPrefixes(),
        )
    }

    @Test
    fun `both platforms detect every common prefix`() {
        val common = documentedPrefixes().common
        assertTrue("Common prefixes documented as shared must be non-empty", common.isNotEmpty())
        assertTrue(
            "Common prefixes missing from Android: ${common - VpnHeuristics.VPN_PREFIXES.toSet()}",
            VpnHeuristics.VPN_PREFIXES.toSet().containsAll(common),
        )
        assertTrue(
            "Common prefixes missing from desktop: ${common - desktopDetectorPrefixes()}",
            desktopDetectorPrefixes().containsAll(common),
        )
    }

    // ── Extractors ────────────────────────────────────────────────────────────

    private data class DocumentedPrefixes(
        val common: Set<String>,
        val desktopOnly: Set<String>,
        val androidOnly: Set<String>,
    )

    /**
     * Parses the three category lines of the "VPN-interface prefixes" section
     * in docs/SECURITY_MODEL.md. Only backtick-wrapped, fully lowercase-
     * alphanumeric tokens count as prefixes — the illustrative `tun*` on the
     * Android-only line is excluded by the `*`, so that line yields the empty set.
     */
    private fun documentedPrefixes(): DocumentedPrefixes {
        val lines = repoFile("docs/SECURITY_MODEL.md").readText().lines()
        fun prefixesOf(label: String): Set<String> {
            val line = lines.firstOrNull { it.contains(label) }
                ?: error("Could not find the '$label' prefix line in SECURITY_MODEL.md")
            return PREFIX_TOKEN.findAll(line).map { it.groupValues[1] }.toSet()
        }
        return DocumentedPrefixes(
            common = prefixesOf("Common (Android + desktop)").also {
                assertTrue("No prefixes parsed from the Common line", it.isNotEmpty())
            },
            desktopOnly = prefixesOf("Desktop-only"),
            androidOnly = prefixesOf("Android-only"),
        )
    }

    /**
     * Pulls the double-quoted string literals from the `_VPN_PREFIXES = ( ... )`
     * tuple in desktop/gatepath/vpn_detector.py (which may span several lines).
     */
    private fun desktopDetectorPrefixes(): Set<String> {
        val text = repoFile("desktop/gatepath/vpn_detector.py").readText()
        val start = text.indexOf("_VPN_PREFIXES")
        require(start >= 0) { "Could not find _VPN_PREFIXES in desktop/gatepath/vpn_detector.py" }
        val open = text.indexOf('(', start)
        val close = text.indexOf(')', open)
        require(open in 0 until close) { "Malformed _VPN_PREFIXES tuple in vpn_detector.py" }
        val prefixes = DOUBLE_QUOTED.findAll(text.substring(open + 1, close))
            .map { it.groupValues[1] }.toSet()
        assertTrue("No quoted prefixes parsed from _VPN_PREFIXES tuple", prefixes.isNotEmpty())
        return prefixes
    }

    private fun repoFile(relative: String): File {
        val explicit = System.getProperty("gatepath.repo.root")
        val root = if (explicit != null) File(explicit) else findRepoRoot()
        val file = File(root, relative)
        require(file.exists()) {
            "$relative not found at $file (set -Dgatepath.repo.root=<repo> or run from android/)"
        }
        return file
    }

    private fun findRepoRoot(): File {
        var dir = File(System.getProperty("user.dir") ?: ".")
        repeat(6) {
            if (File(dir, "docs/SECURITY_MODEL.md").exists()) return dir
            dir = dir.parentFile ?: return dir
        }
        return dir
    }

    private companion object {
        /** A backtick-wrapped, fully lowercase-alphanumeric token (excludes `tun*`). */
        val PREFIX_TOKEN = Regex("`([a-z0-9]+)`")
        val DOUBLE_QUOTED = Regex("\"([^\"]+)\"")
    }
}
