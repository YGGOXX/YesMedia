import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// YesMedia Android：app 模块
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val webgrabRoot: File = rootProject.projectDir.parentFile
// 构建机 Python：WebGrab 工程内的 .venv（Python 3.13，与 Chaquopy 目标版本一致）
val buildPythonPath: String = File(webgrabRoot, ".venv/Scripts/python.exe").absolutePath

fun secret(name: String): String =
    providers.gradleProperty(name).orElse(providers.environmentVariable(name)).orNull.orEmpty()

val keystorePath = secret("YESMEDIA_STORE_FILE")
val keystoreFile: File? = keystorePath.takeIf { it.isNotBlank() }?.let { rootProject.file(it) }
val keystorePassword = secret("YESMEDIA_STORE_PASSWORD")
val keyAliasName = secret("YESMEDIA_KEY_ALIAS")
val keyPassword = secret("YESMEDIA_KEY_PASSWORD")
val releaseSigningReady = keystoreFile?.isFile == true &&
    keystorePassword.isNotBlank() && keyAliasName.isNotBlank() && keyPassword.isNotBlank()

android {
    namespace = "com.webgrab.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.webgrab.app"
        minSdk = 24
        targetSdk = 36
        versionCode = 2
        versionName = "1.0.1"

        // 目标设备只有 arm64（ffmpeg-kit 8.1 也只发布 arm64-v8a / x86_64）
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        create("release") {
            if (releaseSigningReady) {
                storeFile = keystoreFile
                storePassword = keystorePassword
                keyAlias = keyAliasName
                keyPassword = keyPassword
            }
        }
    }

    buildTypes {
        getByName("debug") {
            isMinifyEnabled = false
        }
        getByName("release") {
            isMinifyEnabled = false
            isShrinkResources = false
            if (releaseSigningReady) signingConfig = signingConfigs.getByName("release")
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
            excludes += listOf("META-INF/DEPENDENCIES")
        }
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = true
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
