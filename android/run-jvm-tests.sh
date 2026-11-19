#!/usr/bin/env bash
# run-jvm-tests.sh
#
# Drives the JVM-runnable unit tests for the Gatepath Android module.
# These tests require NO Android SDK or emulator — only JDK 21, kotlinc, and Python 3.
#
# Prerequisites
# ─────────────
#   JDK 21+        : required (java, javac)
#   kotlinc 2.0.x  : required for compilation
#   Python 3       : required for PortalProbeTest (spawns mockportal subprocess)
#
# Tests that can run without kotlinc (pure Python):
#   mockportal/ tests — run via: python3 -m pytest mockportal/
#
# When the Android SDK IS available, prefer:
#   ./gradlew test   (runs all unit tests via the Android Gradle plugin)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ANDROID_ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── Sanity checks ──────────────────────────────────────────────────────────────

if ! command -v java &> /dev/null; then
    echo "ERROR: java not found on PATH. Install JDK 21+." >&2
    exit 1
fi

JAVA_VER=$(java -version 2>&1 | head -1 | grep -oP '(?<=version ")[^"]+')
echo "Java: $JAVA_VER"

if ! command -v kotlinc &> /dev/null; then
    echo ""
    echo "BLOCKED: kotlinc not found on PATH."
    echo ""
    echo "The JVM unit tests are written in Kotlin and require kotlinc to compile."
    echo "Without kotlinc, the tests cannot be run directly on this machine."
    echo ""
    echo "Options:"
    echo "  1. Install Kotlin compiler: https://kotlinlang.org/docs/command-line.html"
    echo "     (SDKMAN: sdk install kotlin)"
    echo "  2. Use the Android SDK + Gradle wrapper:"
    echo "     cd android && ./gradlew test"
    echo "     (requires Android SDK — set ANDROID_HOME)"
    echo "  3. Use a CI environment with both kotlinc and the Android SDK."
    echo ""
    echo "Mockportal Python tests CAN still run:"
    echo "  cd ${REPO_ROOT} && python3 -m pytest mockportal/ -v"
    exit 1
fi

KOTLINC_VER=$(kotlinc -version 2>&1 | head -1)
echo "kotlinc: $KOTLINC_VER"

if ! command -v python3 &> /dev/null; then
    echo "WARNING: python3 not found — PortalProbeTest will fail (needs mockportal)." >&2
fi

# ── Locate dependencies ────────────────────────────────────────────────────────
# For CI: download JARs from Maven Central if not cached.
# Adjust these paths for your local Gradle cache or provide JAR_DIR env var.

JAR_DIR="${JAR_DIR:-${HOME}/.cache/gatepath-test-jars}"
mkdir -p "$JAR_DIR"

KOTLIN_STDLIB="$JAR_DIR/kotlin-stdlib-2.0.21.jar"
KOTLINX_COROUTINES="$JAR_DIR/kotlinx-coroutines-core-jvm-1.9.0.jar"
KOTLINX_SERIALIZATION="$JAR_DIR/kotlinx-serialization-json-jvm-1.7.3.jar"
KOTLINX_SERIALIZATION_CORE="$JAR_DIR/kotlinx-serialization-core-jvm-1.7.3.jar"
JUNIT_JAR="$JAR_DIR/junit-4.13.2.jar"
HAMCREST_JAR="$JAR_DIR/hamcrest-core-1.3.jar"
COROUTINES_TEST="$JAR_DIR/kotlinx-coroutines-test-jvm-1.9.0.jar"
# androidx.lifecycle 2.8.x is multiplatform; the JVM-target artifacts are
# pure JVM jars without Android-only methods, suitable for plain-JVM tests
# of LifecycleObserver/LifecycleRegistry consumers (e.g. BindWatchdogTest).
# core-common is needed transitively by LifecycleRegistry at runtime (it
# provides FastSafeIterableMap).
#
# Naming asymmetry between the two artifacts at 2.8.7:
#   lifecycle-common  ships a `-jvm` variant: lifecycle-common-jvm-2.8.7.jar
#   lifecycle-runtime ships a `-desktop` variant: lifecycle-runtime-desktop-2.8.7.jar
#                     (there is NO lifecycle-runtime-jvm-2.8.7 — 404 at
#                     dl.google.com; the bare lifecycle-runtime-2.8.7.jar
#                     is a KMP umbrella stub with no classes)
LIFECYCLE_COMMON="$JAR_DIR/lifecycle-common-jvm-2.8.7.jar"
LIFECYCLE_RUNTIME="$JAR_DIR/lifecycle-runtime-desktop-2.8.7.jar"
ARCH_CORE_COMMON="$JAR_DIR/core-common-2.2.0.jar"

download_jar() {
    local url="$1"
    local dest="$2"
    local expected="$3"
    if [[ ! -f "$dest" ]]; then
        echo "Downloading $(basename "$dest")..."
        curl -fsSL "$url" -o "$dest"
    fi
    local actual
    actual="$(sha256sum "$dest" | cut -d' ' -f1)"
    if [[ "$actual" != "$expected" ]]; then
        echo "ERROR: sha256 mismatch for $(basename "$dest")" >&2
        echo "  expected: $expected" >&2
        echo "  actual:   $actual" >&2
        echo "  Delete the file and re-run if the cache is stale or corrupt." >&2
        exit 1
    fi
}

MAVEN="https://repo1.maven.org/maven2"
# androidx artifacts are not on Maven Central; pull them from Google's mirror.
GOOGLE_MAVEN="https://dl.google.com/dl/android/maven2"
download_jar "$MAVEN/org/jetbrains/kotlin/kotlin-stdlib/2.0.21/kotlin-stdlib-2.0.21.jar" "$KOTLIN_STDLIB" "f31cc53f105a7e48c093683bbd5437561d1233920513774b470805641bedbc09"
download_jar "$MAVEN/org/jetbrains/kotlinx/kotlinx-coroutines-core-jvm/1.9.0/kotlinx-coroutines-core-jvm-1.9.0.jar" "$KOTLINX_COROUTINES" "ad89c2892235e670f222d819cb3d81188143cb19a05b59df9889ae4269f5c70a"
download_jar "$MAVEN/org/jetbrains/kotlinx/kotlinx-serialization-json-jvm/1.7.3/kotlinx-serialization-json-jvm-1.7.3.jar" "$KOTLINX_SERIALIZATION" "b1e9138499ed8d20375edda3f2b1c95f3103a258eff6af9edc5ea07100f2b29c"
download_jar "$MAVEN/org/jetbrains/kotlinx/kotlinx-serialization-core-jvm/1.7.3/kotlinx-serialization-core-jvm-1.7.3.jar" "$KOTLINX_SERIALIZATION_CORE" "f0adde45864144475385cf4aa7e0b7feb27f61fcf9472665ed98cc971b06b1eb"
download_jar "$MAVEN/junit/junit/4.13.2/junit-4.13.2.jar" "$JUNIT_JAR" "8e495b634469d64fb8acfa3495a065cbacc8a0fff55ce1e31007be4c16dc57d3"
download_jar "$MAVEN/org/hamcrest/hamcrest-core/1.3/hamcrest-core-1.3.jar" "$HAMCREST_JAR" "66fdef91e9739348df7a096aa384a5685f4e875584cce89386a7a47251c4d8e9"
download_jar "$MAVEN/org/jetbrains/kotlinx/kotlinx-coroutines-test-jvm/1.9.0/kotlinx-coroutines-test-jvm-1.9.0.jar" "$COROUTINES_TEST" "68b87fa90db3dab1e794ff6078364087c07f87e5e4a4d0c8d3272a222ee8fe7e"
download_jar "$GOOGLE_MAVEN/androidx/lifecycle/lifecycle-common-jvm/2.8.7/lifecycle-common-jvm-2.8.7.jar" "$LIFECYCLE_COMMON" "61c873a7327c946ec033c310bb98f3f92eeabcede0e1a5200ab8a1896483c7bf"
download_jar "$GOOGLE_MAVEN/androidx/lifecycle/lifecycle-runtime-desktop/2.8.7/lifecycle-runtime-desktop-2.8.7.jar" "$LIFECYCLE_RUNTIME" "10bfe53bb2752f967751a5102cdb785eeea54e1d4df6bde88fb0f50b0a49156c"
download_jar "$GOOGLE_MAVEN/androidx/arch/core/core-common/2.2.0/core-common-2.2.0.jar" "$ARCH_CORE_COMMON" "65308a06b1c00ee186cb9e19321383f043b993813f1522c47f4a3e3303bdba41"

# ── Compile ────────────────────────────────────────────────────────────────────

SRC_MAIN="$ANDROID_ROOT/app/src/main/java"
SRC_TEST="$ANDROID_ROOT/app/src/test/java"
# Overridable so several checkouts (or parallel workers on one checkout) can
# run the suite without clobbering each other's compiled classes.
BUILD_DIR="${GATEPATH_JVM_TEST_BUILD_DIR:-$ANDROID_ROOT/build/jvm-test}"
CLASSES_MAIN="$BUILD_DIR/classes/main"
CLASSES_TEST="$BUILD_DIR/classes/test"

mkdir -p "$CLASSES_MAIN" "$CLASSES_TEST"

# Source files that are compilable on plain JVM (no android.* imports)
MAIN_SOURCES=(
    "$SRC_MAIN/com/ventouxlabs/gatepath/audit/AuditEntry.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/audit/AuditLog.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/BlockedDomains.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/ProbeErrorReason.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/PortalProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/PortalRedirectHint.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/PortalProbeCapture.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/session/PortalSession.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/session/PortalSessionManager.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/ui/WebViewHostMatching.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/ui/SslErrorPolicy.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/ui/PortalLoadError.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/ui/ConfinementStateText.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/VpnHeuristics.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/VpnKind.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/NetworkBinder.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/ProcessBinding.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/ProbePath.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/ConfinementState.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/CaptivePortalReply.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/UnknownReason.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/BoundedReader.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/HttpFetcher.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/network/NetworkDiagnostics.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/DiagnosticReport.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/RecommendedAction.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/ProbeContext.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/DiagnosticProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/DiagnosticEngine.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/PrivateDnsProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/HttpProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/VpnProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/HttpProxyProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/NoDnsProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/CellularFallbackProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/RedirectLoopProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/ClockSkewProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/HttpsOnlyProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/DnsHijackProbe.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/ConsoleCaptureEntry.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/ConsoleCaptureBuffer.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/ConsoleCaptureFile.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/DiagnosticsBundle.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/CertSummary.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/IncidentEvidence.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/LogRedaction.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/SignInTimeline.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/diag/SignInTimelineStore.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/session/IncidentTracker.kt"
    "$SRC_MAIN/com/ventouxlabs/gatepath/BindWatchdog.kt"
)

MAIN_CP="$KOTLIN_STDLIB:$KOTLINX_COROUTINES:$KOTLINX_SERIALIZATION:$KOTLINX_SERIALIZATION_CORE:$LIFECYCLE_COMMON:$LIFECYCLE_RUNTIME:$ARCH_CORE_COMMON"

# Locate the kotlinx-serialization compiler plugin shipped with kotlinc.
KOTLINC_HOME="$(dirname "$(dirname "$(command -v kotlinc)")")"
SERIALIZATION_PLUGIN="$KOTLINC_HOME/lib/kotlinx-serialization-compiler-plugin.jar"
if [[ ! -f "$SERIALIZATION_PLUGIN" ]]; then
    echo "ERROR: kotlinx-serialization-compiler-plugin.jar not found at $SERIALIZATION_PLUGIN" >&2
    echo "       (looked relative to kotlinc at $(command -v kotlinc))" >&2
    exit 1
fi

echo ""
echo "=== Compiling main sources (JVM-compatible subset) ==="
# Stub out android.util.Log so AuditLog.kt compiles without Android SDK
ANDROID_STUB="$BUILD_DIR/android-stub"
mkdir -p "$ANDROID_STUB/android/util" "$ANDROID_STUB/android/net" "$ANDROID_STUB/android/system"
# android.system.ErrnoException stub so ProbeErrorReason.kt's production errno
# reader compiles. Only the members the main sources touch. Tests must NOT
# construct ErrnoException: under Gradle the real android.jar stub's
# constructor is a no-op and `errno` always reads 0, so any such test passes
# here and fails there. probeErrorReason takes an errno-reader function for
# exactly that reason; tests supply their own throwable type. There is no
# OsConstants stub either: its fields are natively initialised and read as 0
# under Gradle, so the main sources carry their own LinuxErrno constants.
cat > "$ANDROID_STUB/android/system/ErrnoException.java" << 'JAVA_EOF'
package android.system;
public class ErrnoException extends Exception {
    public final int errno;
    public final String functionName;
    public ErrnoException(String functionName, int errno) {
        super(functionName + " failed: errno " + errno);
        this.functionName = functionName;
        this.errno = errno;
    }
}
JAVA_EOF
cat > "$ANDROID_STUB/android/util/Log.java" << 'JAVA_EOF'
package android.util;
public class Log {
    public static int d(String tag, String msg) { System.out.println("[D/" + tag + "] " + msg); return 0; }
    public static int e(String tag, String msg) { System.err.println("[E/" + tag + "] " + msg); return 0; }
    public static int w(String tag, String msg) { System.out.println("[W/" + tag + "] " + msg); return 0; }
    public static int i(String tag, String msg) { System.out.println("[I/" + tag + "] " + msg); return 0; }
}
JAVA_EOF
# Minimal android.net.Network stub so PortalProbe.kt compiles on plain JVM.
# The real Android class scopes sockets to a Network; in JVM tests, we route
# through the default JVM stack by delegating openConnection() back to the URL.
cat > "$ANDROID_STUB/android/net/Network.java" << 'JAVA_EOF'
package android.net;
import java.io.IOException;
import java.net.URL;
import java.net.URLConnection;
public class Network {
    public URLConnection openConnection(URL url) throws IOException {
        return url.openConnection();
    }
}
JAVA_EOF
javac -d "$ANDROID_STUB" \
    "$ANDROID_STUB/android/util/Log.java" \
    "$ANDROID_STUB/android/net/Network.java" \
    "$ANDROID_STUB/android/system/ErrnoException.java"

kotlinc \
    -Xplugin="$SERIALIZATION_PLUGIN" \
    -classpath "$MAIN_CP:$ANDROID_STUB" \
    -d "$CLASSES_MAIN" \
    "${MAIN_SOURCES[@]}"

echo ""
echo "=== Compiling test sources ==="
TEST_SOURCES=(
    "$SRC_TEST/com/ventouxlabs/gatepath/BlockedDomainsTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/SessionStateTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/session/IncidentTrackerTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/AuditLogTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/AuditSchemaParityTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/AuditTailParityTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/PortalProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/ProbeErrorReasonTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/PortalRedirectHintTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/PortalProbeCaptureTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/HttpFetcherTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/WebViewHostMatchingTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/SslErrorPolicyTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/PortalLoadErrorTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/ConfinementStateTextTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/BindWatchdogTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/VpnHeuristicsTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/VpnKindTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/ProcessBindingTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/ConfinementStateTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/CaptivePortalReplyTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/MainActivityManifestTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/LogRedactionTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/RedactionPatternsTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/SignInTimelineTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/VpnPrefixParityTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/BoundedReaderTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/DiagnosticEngineTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/ProbeContextTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/PrivateDnsProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/HttpProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/VpnProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/HttpProxyProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/NoDnsProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/CellularFallbackProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/RedirectLoopProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/ClockSkewProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/HttpsOnlyProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/DnsHijackProbeTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/ConsoleCaptureBufferTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/ConsoleCaptureFileTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/DiagnosticsBundleTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/CertSummaryTest.kt"
    "$SRC_TEST/com/ventouxlabs/gatepath/diag/IncidentEvidenceTest.kt"
)

TEST_CP="$MAIN_CP:$CLASSES_MAIN:$ANDROID_STUB:$JUNIT_JAR:$HAMCREST_JAR:$COROUTINES_TEST"

# -Xfriend-paths makes the main output a "friend" of the test compilation, which
# is what the Kotlin Gradle plugin does for test source sets. Without it, main
# and test are two unrelated modules here and Gradle silently accepts code this
# script rejects: `internal` declarations are invisible (so their unit tests
# cannot be written at all), and a smart cast on a main-module property fails
# with "declared in different module". Both are artefacts of how this script
# compiles, not real problems with the code, so keep the two paths in agreement.
kotlinc \
    -Xplugin="$SERIALIZATION_PLUGIN" \
    -Xfriend-paths="$CLASSES_MAIN" \
    -classpath "$TEST_CP" \
    -d "$CLASSES_TEST" \
    "${TEST_SOURCES[@]}"

# ── Run via JUnit console launcher ─────────────────────────────────────────────

JUNIT_LAUNCHER="$JAR_DIR/junit-platform-console-standalone-1.10.2.jar"
download_jar \
    "$MAVEN/org/junit/platform/junit-platform-console-standalone/1.10.2/junit-platform-console-standalone-1.10.2.jar" \
    "$JUNIT_LAUNCHER" \
    "a1de557821293ce903c213c694165fff532cf92081bac4238b9e05b35f04f43f"

# The standalone JAR bundles junit-vintage-engine, so JUnit 4 @Test methods are
# discovered by the Platform launcher.

FULL_CP="$TEST_CP:$CLASSES_TEST:$JUNIT_LAUNCHER"

echo ""
echo "=== Running JVM unit tests ==="
java \
    -Dgatepath.repo.root="$REPO_ROOT" \
    -jar "$JUNIT_LAUNCHER" \
    execute \
    --class-path "$TEST_CP:$CLASSES_TEST" \
    --scan-class-path \
    --fail-if-no-tests \
    --details=tree

echo ""
echo "=== All JVM tests completed ==="
