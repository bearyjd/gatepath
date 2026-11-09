package com.ventouxlabs.gatepath

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element

/**
 * Guard for issue #190: without `configChanges`, a rotation or fold recreates
 * `MainActivity`. The old composition's `GatepathWebView.onDispose` then wipes
 * the process-wide cookies and storage (`clearPortalSessionState`) while the
 * ViewModel still holds the active session, so the new WebView reloads the
 * portal from its first page with no session. `density` covers a foldable
 * whose inner and outer screens differ.
 */
class MainActivityManifestTest {

    @Test
    fun `a fold or rotation does not rebuild the in-app sign-in screen`() {
        val android = "http://schemas.android.com/apk/res/android"
        val manifest = File(mainRoot(), "AndroidManifest.xml")
        val document = DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
            .newDocumentBuilder().parse(manifest)
        val activities = document.getElementsByTagName("activity")
        val main = (0 until activities.length).map { activities.item(it) as Element }
            .single { it.getAttributeNS(android, "name") == ".MainActivity" }
        val handled = main.getAttributeNS(android, "configChanges").split('|').toSet()
        for (change in listOf("orientation", "screenSize", "smallestScreenSize", "screenLayout", "density")) {
            assertTrue("MainActivity must handle $change itself (declares: $handled)", change in handled)
        }
    }

    /** `android/app/src/main`, from `-Dgatepath.repo.root` or by walking up from the working directory. */
    private fun mainRoot(): File {
        val relative = "android/app/src/main"
        System.getProperty("gatepath.repo.root")?.let { root ->
            File(root, relative).takeIf { it.isDirectory }?.let { return it }
        }
        var dir: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        while (dir != null) {
            File(dir, relative).takeIf { it.isDirectory }?.let { return it }
            dir = dir.parentFile
        }
        throw AssertionError("$relative not found (set -Dgatepath.repo.root=<repo>)")
    }
}
