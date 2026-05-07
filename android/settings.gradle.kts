pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
    // Gradle picks up `gradle/libs.versions.toml` as the default `libs` version
    // catalog automatically — explicitly creating one with the same name fails
    // with "you can only call 'from' a single time" because the auto-import
    // already happened.
}

rootProject.name = "gatepath"
include(":app")
