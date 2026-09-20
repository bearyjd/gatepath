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

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }
}

// Debug only, enforced rather than by policy: this app exists solely for the
// android-e2e no-leak proof and must never produce a shippable APK. AGP always
// registers a `release` build type, so merely not declaring one is not enough —
// `:testvpn:assembleRelease` would still build a real VPN. Disabling the variant
// removes those tasks entirely. Nothing needs it: android-e2e.yml runs
// `:testvpn:assembleDebug` and `:testvpn:processDebugManifest` only.
androidComponents {
    beforeVariants(selector().withBuildType("release")) { variant ->
        variant.enable = false
    }
}

dependencies {
    // org.json is part of the Android platform; no third-party dependencies.
}
