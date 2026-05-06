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

download_jar() {
    local url="$1"
    local dest="$2"
    if [[ ! -f "$dest" ]]; then
        echo "Downloading $(basename "$dest")..."
        curl -fsSL "$url" -o "$dest"
    fi
}

MAVEN="https://repo1.maven.org/maven2"
download_jar "$MAVEN/org/jetbrains/kotlin/kotlin-stdlib/2.0.21/kotlin-stdlib-2.0.21.jar" "$KOTLIN_STDLIB"
download_jar "$MAVEN/org/jetbrains/kotlinx/kotlinx-coroutines-core-jvm/1.9.0/kotlinx-coroutines-core-jvm-1.9.0.jar" "$KOTLINX_COROUTINES"
download_jar "$MAVEN/org/jetbrains/kotlinx/kotlinx-serialization-json-jvm/1.7.3/kotlinx-serialization-json-jvm-1.7.3.jar" "$KOTLINX_SERIALIZATION"
download_jar "$MAVEN/org/jetbrains/kotlinx/kotlinx-serialization-core-jvm/1.7.3/kotlinx-serialization-core-jvm-1.7.3.jar" "$KOTLINX_SERIALIZATION_CORE"
download_jar "$MAVEN/junit/junit/4.13.2/junit-4.13.2.jar" "$JUNIT_JAR"
download_jar "$MAVEN/org/hamcrest/hamcrest-core/1.3/hamcrest-core-1.3.jar" "$HAMCREST_JAR"
download_jar "$MAVEN/org/jetbrains/kotlinx/kotlinx-coroutines-test-jvm/1.9.0/kotlinx-coroutines-test-jvm-1.9.0.jar" "$COROUTINES_TEST"

# ── Compile ────────────────────────────────────────────────────────────────────

SRC_MAIN="$ANDROID_ROOT/app/src/main/java"
SRC_TEST="$ANDROID_ROOT/app/src/test/java"
BUILD_DIR="$ANDROID_ROOT/build/jvm-test"
CLASSES_MAIN="$BUILD_DIR/classes/main"
CLASSES_TEST="$BUILD_DIR/classes/test"

mkdir -p "$CLASSES_MAIN" "$CLASSES_TEST"

# Source files that are compilable on plain JVM (no android.* imports)
MAIN_SOURCES=(
    "$SRC_MAIN/cc/grepon/gatepath/audit/AuditEntry.kt"
    "$SRC_MAIN/cc/grepon/gatepath/audit/AuditLog.kt"
    "$SRC_MAIN/cc/grepon/gatepath/network/BlockedDomains.kt"
    "$SRC_MAIN/cc/grepon/gatepath/network/PortalProbe.kt"
    "$SRC_MAIN/cc/grepon/gatepath/session/PortalSession.kt"
    "$SRC_MAIN/cc/grepon/gatepath/session/PortalSessionManager.kt"
)

MAIN_CP="$KOTLIN_STDLIB:$KOTLINX_COROUTINES:$KOTLINX_SERIALIZATION:$KOTLINX_SERIALIZATION_CORE"

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
mkdir -p "$ANDROID_STUB/android/util" "$ANDROID_STUB/android/net"
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
javac -d "$ANDROID_STUB" "$ANDROID_STUB/android/util/Log.java" "$ANDROID_STUB/android/net/Network.java"

kotlinc \
    -Xplugin="$SERIALIZATION_PLUGIN" \
    -classpath "$MAIN_CP:$ANDROID_STUB" \
    -d "$CLASSES_MAIN" \
    "${MAIN_SOURCES[@]}"

echo ""
echo "=== Compiling test sources ==="
TEST_SOURCES=(
    "$SRC_TEST/cc/grepon/gatepath/BlockedDomainsTest.kt"
    "$SRC_TEST/cc/grepon/gatepath/SessionStateTest.kt"
    "$SRC_TEST/cc/grepon/gatepath/AuditLogTest.kt"
    "$SRC_TEST/cc/grepon/gatepath/AuditSchemaParityTest.kt"
    "$SRC_TEST/cc/grepon/gatepath/PortalProbeTest.kt"
)

TEST_CP="$MAIN_CP:$CLASSES_MAIN:$ANDROID_STUB:$JUNIT_JAR:$HAMCREST_JAR:$COROUTINES_TEST"

kotlinc \
    -Xplugin="$SERIALIZATION_PLUGIN" \
    -classpath "$TEST_CP" \
    -d "$CLASSES_TEST" \
    "${TEST_SOURCES[@]}"

# ── Run via JUnit console launcher ─────────────────────────────────────────────

JUNIT_LAUNCHER="$JAR_DIR/junit-platform-console-standalone-1.10.2.jar"
download_jar \
    "$MAVEN/org/junit/platform/junit-platform-console-standalone/1.10.2/junit-platform-console-standalone-1.10.2.jar" \
    "$JUNIT_LAUNCHER"

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
