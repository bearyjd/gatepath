plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.hilt)
    alias(libs.plugins.ksp)
}

android {
    namespace = "cc.grepon.gatepath"
    compileSdk = 35

    defaultConfig {
        applicationId = "cc.grepon.gatepath"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
    }

    // Release signing reads the keystore from the environment, so no secrets
    // live in the repo. The release workflow sets these from GitHub secrets
    // (see docs/RELEASING.md). When the keystore env is absent (local builds,
    // PR CI), the release build is left UNSIGNED so `assembleRelease` still runs.
    val keystoreFile = System.getenv("GATEPATH_KEYSTORE_FILE")?.takeIf { it.isNotBlank() }
    signingConfigs {
        create("release") {
            if (keystoreFile != null) {
                storeFile = file(keystoreFile)
                // Fail fast with a readable message if a keystore is configured
                // but a companion secret is missing (vs. a cryptic AGP error).
                storePassword = requireNotNull(System.getenv("GATEPATH_KEYSTORE_PASSWORD")) {
                    "GATEPATH_KEYSTORE_PASSWORD is required when a keystore is configured"
                }
                keyAlias = requireNotNull(System.getenv("GATEPATH_KEY_ALIAS")) {
                    "GATEPATH_KEY_ALIAS is required when a keystore is configured"
                }
                keyPassword = requireNotNull(System.getenv("GATEPATH_KEY_PASSWORD")) {
                    "GATEPATH_KEY_PASSWORD is required when a keystore is configured"
                }
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // Signed only when a keystore was provided; otherwise unsigned so
            // CI/PR builds and local `assembleRelease` keep working.
            signingConfig = keystoreFile?.let { signingConfigs.getByName("release") }
        }
        debug {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }

    kotlinOptions {
        jvmTarget = "21"
    }

    buildFeatures {
        compose = true
        // Generate BuildConfig so we can gate diagnostic logging on
        // BuildConfig.DEBUG. AGP 8+ disables this by default.
        buildConfig = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.process)
    implementation(libs.androidx.activity.compose)

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)

    implementation(libs.hilt.android)
    ksp(libs.hilt.android.compiler)
    implementation(libs.hilt.navigation.compose)

    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    debugImplementation(libs.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
}
