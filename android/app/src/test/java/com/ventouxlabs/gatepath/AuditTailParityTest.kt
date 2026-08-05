package com.ventouxlabs.gatepath

import com.ventouxlabs.gatepath.audit.BUNDLE_TAIL_LIMIT
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/**
 * Drift guard: the Android bundle and the desktop collector must carry the same
 * number of audit entries.
 *
 * The two sides already agreed on *which* fields to scrub and silently
 * disagreed on *how much* to send — desktop capped at 5000, Android sent the
 * entire log. Redacting SSIDs while still handing over an unbounded history is
 * a strange kind of privacy, so the volume is now a contract too, and this
 * test is what keeps it one.
 *
 * Parses the shell script rather than duplicating the number, in the style of
 * `AuditSchemaParityTest` and `test_netns_client.py`'s Rust-variant round-trip.
 */
class AuditTailParityTest {

    @Test
    fun `android bundle cap matches the desktop collector's AUDIT_TAIL`() {
        val script = File(repoRoot(), COLLECTOR)
        require(script.exists()) {
            "$COLLECTOR not found at $script (set -Dgatepath.repo.root=<repo> or run from android/)"
        }

        // The assignment is quoted in the script: AUDIT_TAIL="${AUDIT_TAIL:-5000}"
        val declaration = Regex("""AUDIT_TAIL="?\$\{AUDIT_TAIL:-(\d+)\}""")
            .find(script.readText())
        requireNotNull(declaration) {
            "Could not find an AUDIT_TAIL default in $COLLECTOR. If the collector " +
                "stopped bounding its audit tail, decide deliberately whether Android " +
                "should still bound its own — do not just delete this guard."
        }

        assertEquals(
            "Android's BUNDLE_TAIL_LIMIT and the desktop collector's AUDIT_TAIL have " +
                "drifted. Both decide how much audit history a user hands over when they " +
                "share diagnostics; they are the same promise on two platforms.",
            declaration.groupValues[1].toInt(),
            BUNDLE_TAIL_LIMIT,
        )
    }

    private companion object {
        const val COLLECTOR = "desktop/gatepath-netns-helper/packaging/collect-diagnostics.sh"

        fun repoRoot(): File {
            System.getProperty("gatepath.repo.root")?.let { return File(it) }
            var dir = File(System.getProperty("user.dir") ?: ".").absoluteFile
            while (!File(dir, ".git").exists() && dir.parentFile != null) {
                dir = dir.parentFile
            }
            return dir
        }
    }
}
