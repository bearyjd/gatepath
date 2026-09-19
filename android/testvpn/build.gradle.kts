plugins {
    alias(libs.plugins.android.application)
    // Kotlin support is built into AGP since 9.0 — kotlin("android") must NOT
    // be applied (AGP 9 hard-fails on it). https://kotl.in/gradle/agp-built-in-kotlin
}

android {
    namespace = "com.ventouxlabs.gatepath.testvpn"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.ventouxlabs.gatepath.testvpn"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "e2e"
    }

    buildTypes {
        // Debug only by policy: this app exists solely for the android-e2e no-leak
        // proof and is never installed on a real device or published. `release` is
        // declared only so `:testvpn:processReleaseManifest` resolves like any other
        // module — the android-e2e workflow never assembles or ships it.
        release { isMinifyEnabled = false }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }
}

dependencies {
    // org.json is part of the Android platform; no third-party dependencies.
}
