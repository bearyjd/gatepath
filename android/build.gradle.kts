// Top-level build file: plugins block only. No other configuration here.
plugins {
    alias(libs.plugins.android.application) apply false
    // kotlin("android") removed: built into AGP since 9.0.
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    alias(libs.plugins.hilt) apply false
    alias(libs.plugins.ksp) apply false
}
