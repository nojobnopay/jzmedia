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
        google {
            content { excludeGroup("io.github.peerless2012") }
        }
        mavenCentral()
    }
}

rootProject.name = "jzmedia-tv"
include(":app")
