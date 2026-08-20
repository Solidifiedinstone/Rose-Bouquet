pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // NewPipeExtractor — the YouTube half — publishes here and nowhere else.
        maven("https://jitpack.io")
    }
}

rootProject.name = "Rose Bouquet"
include(":app")
