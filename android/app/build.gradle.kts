import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// YesMedia Android app module
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val webgrabRoot: File = rootProject.projectDir.parentFile
// Build Python: project-local Python 3.13 virtual environment, matching Chaquopy.
val buildPythonPath: String = System.getenv("WEBGRAB_BUILD_PYTHON")
    ?: File(webgrabRoot, ".venv/Scripts/python.exe").absolutePath

val keystoreFile: File = rootProject.file(
    System.getenv("WEBGRAB_STORE_FILE")
        ?: (findProperty("WEBGRAB_STORE_FILE") as String?)
        ?: "keystore/webgrab.jks"
)
val keystorePassword: String = System.getenv("WEBGRAB_STORE_PASSWORD")
    ?: (findProperty("WEBGRAB_STORE_PASSWORD") as String?) ?: ""
val keyAliasName: String = System.getenv("WEBGRAB_KEY_ALIAS")
    ?: (findProperty("WEBGRAB_KEY_ALIAS") as String?) ?: ""
val releaseKeyPassword: String = System.getenv("WEBGRAB_KEY_PASSWORD")
    ?: (findProperty("WEBGRAB_KEY_PASSWORD") as String?) ?: keystorePassword
val releaseSigningConfigured = keystoreFile.isFile && keystorePassword.isNotBlank() &&
    keyAliasName.isNotBlank() && releaseKeyPassword.isNotBlank()

if (gradle.startParameter.taskNames.any { it.contains("release", ignoreCase = true) } &&
    !releaseSigningConfigured
) {
    throw GradleException(
        "Release signing is not configured. Provide WEBGRAB_STORE_FILE, " +
            "WEBGRAB_STORE_PASSWORD, WEBGRAB_KEY_ALIAS and WEBGRAB_KEY_PASSWORD " +
            "through environment variables or private Gradle properties. No unsigned release APK was produced."
    )
}

gradle.taskGraph.whenReady {
    if (allTasks.any { it.name.contains("Release", ignoreCase = true) } &&
        !releaseSigningConfigured
    ) {
        throw GradleException(
            "Release signing is not configured. Provide WEBGRAB_STORE_FILE, " +
                "WEBGRAB_STORE_PASSWORD, WEBGRAB_KEY_ALIAS and WEBGRAB_KEY_PASSWORD " +
                "through environment variables or private Gradle properties. No unsigned release APK was produced."
        )
    }
}

android {
    namespace = "com.webgrab.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.webgrab.app"
        minSdk = 24
        targetSdk = 35
        versionCode = 2
        versionName = "1.0.1"

        // Both ABIs are present in Chaquopy 17.2.0 Python runtimes and
        // ffmpeg-kit-full 8.1.9. Retain aapt/ZIP checks in release QA.
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        create("release") {
            storeFile = keystoreFile
            storePassword = keystorePassword
            keyAlias = keyAliasName
            keyPassword = releaseKeyPassword
        }
    }

    buildTypes {
        getByName("debug") {
            isMinifyEnabled = false
        }
        getByName("release") {
            isMinifyEnabled = false
            isShrinkResources = false
            if (releaseSigningConfigured) signingConfig = signingConfigs.getByName("release")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        buildConfig = true
    }

    packaging {
        jniLibs {
            // 16KB 页对齐：native 库不压缩、安装时直接 mmap（AGP 8.5.1+ 会按 16KB 对齐 .so）
            useLegacyPackaging = false
            // ffmpeg-kit 与 Chaquopy 都可能带 libc++_shared.so，保留第一个
            pickFirsts += listOf("**/libc++_shared.so")
        }
        resources {
            excludes += listOf(
                "META-INF/DEPENDENCIES",
                "META-INF/LICENSE",
                "META-INF/LICENSE.txt",
                "META-INF/NOTICE",
                "META-INF/NOTICE.txt",
            )
        }
    }

    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        buildPython(buildPythonPath)
        pip {
            // Direct pypi.org hangs on this network; use the Tsinghua mirror.
            // Build-time only: no index URL ends up in the APK.
            options("--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple")
            options("--retries", "5")
            install("requests")
            install("beautifulsoup4")
            install("yt-dlp")
        }
    }
    // Python 源码目录用 Chaquopy 默认值 app/src/main/python（契约要求同一路径）；
    // 该目录由 android/copy_python.ps1 从 core/webgrab 复制生成，勿手改。
}

dependencies {
    // LGPL 版 ffmpeg-kit（维护分支，SDK 35 + 16KB 页对齐）
    implementation("dev.ffmpegkit-maintained:ffmpeg-kit-full:8.1.9")

    // 抖音原生解析的纯 JVM 单测（不进 APK）
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
