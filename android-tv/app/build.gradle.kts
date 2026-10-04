import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

val appVersion = Properties().apply {
    rootProject.file("../version.properties").inputStream().use { load(it) }
}
val appVersionName = appVersion.getProperty("versionName") ?: error("Missing versionName")
val appVersionCode = appVersion.getProperty("versionCode")?.toIntOrNull() ?: error("Invalid versionCode")
require(Regex("(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)").matches(appVersionName)) {
    "versionName must be X.Y.Z; build type supplies the debug suffix"
}
require(appVersionCode in 1..2_100_000_000) { "versionCode must be a positive Android version code" }

fun gitOutput(vararg args: String): String? = runCatching {
    val result = providers.exec {
        workingDir(rootProject.projectDir.parentFile)
        commandLine("git", *args)
        isIgnoreExitValue = true
    }
    if (result.result.get().exitValue == 0) result.standardOutput.asText.get().trim() else null
}.getOrNull()

val sourceCommit = gitOutput("rev-parse", "HEAD")
val gitCommit = providers.gradleProperty("jzmediaGitCommit").orNull ?: sourceCommit ?: "unknown"
require(gitCommit == "unknown" || Regex("[0-9a-f]{40}|[0-9a-f]{64}").matches(gitCommit)) {
    "jzmediaGitCommit must be a full Git commit hash"
}
require(sourceCommit == null || gitCommit == sourceCommit) {
    "jzmediaGitCommit differs from the checked-out source"
}
val sourceDirty = gitOutput("status", "--porcelain")?.let { it.isNotEmpty() }
val requestedDirty = providers.gradleProperty("jzmediaGitDirty").orNull
require(requestedDirty == null || requestedDirty in listOf("true", "false", "unknown")) {
    "jzmediaGitDirty must be true, false or unknown"
}
val gitDirty = when (requestedDirty) {
    "true" -> true
    "false" -> false
    "unknown" -> null
    else -> sourceDirty
}
require(sourceDirty == null || gitDirty == sourceDirty) {
    "jzmediaGitDirty differs from the checked-out source"
}
val releaseSigning = listOf("KEYSTORE", "STORE_PASSWORD", "KEY_ALIAS", "KEY_PASSWORD")
    .associateWith { providers.environmentVariable("JZMEDIA_ANDROID_$it").orNull }
require(releaseSigning.values.all { it.isNullOrBlank() } || releaseSigning.values.none { it.isNullOrBlank() }) {
    "Set all four JZMEDIA_ANDROID_KEYSTORE / STORE_PASSWORD / KEY_ALIAS / KEY_PASSWORD values for release signing"
}
val hasReleaseSigning = releaseSigning.values.none { it.isNullOrBlank() }

// This record travels inside the APK, so exporting cannot relabel stale build output.
val buildRecord = """{"versionName":"$appVersionName","versionCode":$appVersionCode,"source":{"commit":${if (gitCommit == "unknown") "null" else "\"$gitCommit\""},"dirty":${gitDirty ?: "null"}}}"""
abstract class GenerateJzmediaBuildRecord : DefaultTask() {
    @get:Input
    abstract val record: Property<String>

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @TaskAction
    fun generate() {
        outputDirectory.get().file("jzmedia-build.json").asFile.apply {
            parentFile.mkdirs()
            writeText(record.get() + "\n")
        }
    }
}

android {
    namespace = "org.jzmedia.tv"
    compileSdk = 36
    buildToolsVersion = "36.0.0"

    defaultConfig {
        applicationId = "org.jzmedia.tv"
        minSdk = 23
        targetSdk = 36
        // Shared with the server and web through the repository's version.properties.
        versionCode = appVersionCode
        versionName = appVersionName
        buildConfigField("String", "GIT_COMMIT", "\"$gitCommit\"")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    if (hasReleaseSigning) {
        signingConfigs.create("distribution") {
            storeFile = file(releaseSigning.getValue("KEYSTORE")!!)
            storePassword = releaseSigning.getValue("STORE_PASSWORD")
            keyAlias = releaseSigning.getValue("KEY_ALIAS")
            keyPassword = releaseSigning.getValue("KEY_PASSWORD")
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            // No key supplied means an explicitly unsigned build, never debug signing.
            if (hasReleaseSigning) signingConfig = signingConfigs.getByName("distribution")
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
}

androidComponents {
    onVariants { variant ->
        val task = tasks.register<GenerateJzmediaBuildRecord>(
            "generate${variant.name.replaceFirstChar { it.uppercase() }}JzmediaBuildRecord"
        ) {
            record.set(buildRecord)
        }
        variant.sources.assets?.addGeneratedSourceDirectory(task, GenerateJzmediaBuildRecord::outputDirectory)
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.tv.material)
    implementation(libs.androidx.media3.exoplayer)
    implementation(libs.androidx.media3.hls)
    implementation(libs.androidx.media3.ui)
    implementation(libs.androidx.media3.session)
    implementation(libs.androidx.media3.okhttp)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.coroutines.android)
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation("org.json:json:20250517")
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.test)
    androidTestImplementation(libs.androidx.test.runner)
    debugImplementation(libs.androidx.compose.test.manifest)
}
