# YesMedia Android 1.0.1

The Android app embeds the `webgrab` Python module through Chaquopy and serves its UI over a loopback-only HTTP server inside a WebView. The package and persistent identifiers remain `com.webgrab.app` and `webgrab` for upgrade compatibility. `SharedPreferences` remains `webgrab`.

## Android configuration

- `applicationId` / Kotlin package: `com.webgrab.app`
- `versionName`: `1.0.1`; `versionCode`: `2` (previous main used code 1)
- `minSdk`: 24; `targetSdk` and `compileSdk`: 35
- Gradle wrapper: 8.13; AGP: 8.9.2; Kotlin: 2.2.0; Chaquopy: 17.2.0
- Chaquopy Python: 3.13; `requests`, `beautifulsoup4`, and `yt-dlp` are installed during build
- FFmpeg: `dev.ffmpegkit-maintained:ffmpeg-kit-full:8.1.9`
- ABI filters request `arm64-v8a` and `x86_64`. This is a build configuration, not evidence of a successful APK or device test; inspect the produced APK and test each target before making a release compatibility claim.

## Build

From repository root in Windows PowerShell:

```powershell
.\build_apk.ps1 -DebugBuild
.\build_apk.ps1
```

The script uses the Gradle wrapper, detects JDK 17 and Android SDK 35 via environment variables or standard installation locations, and requires Python 3.13. It writes `dist/YesMedia-1.0.1-debug.apk` or `dist/YesMedia-1.0.1-release.apk` with a matching SHA-256 file. Debug and release artifacts do not overwrite one another.

Release signing is required. Supply `WEBGRAB_STORE_FILE`, `WEBGRAB_STORE_PASSWORD`, `WEBGRAB_KEY_ALIAS`, and `WEBGRAB_KEY_PASSWORD` as environment variables or private Gradle properties (for example in the user-level `~/.gradle/gradle.properties`). Do not commit keystores or credentials. The build fails rather than producing an unsigned APK when signing is missing. The repository's previous committed signing-property values have been removed from the current tree; rotate any credential which may have been reused. This does not erase old Git history.

To inspect an APK after building:

```powershell
$apk = '.\dist\YesMedia-1.0.1-debug.apk'
& "$env:ANDROID_SDK_ROOT\build-tools\35.0.0\aapt2.exe" dump badging $apk
adb install -r $apk
adb shell am start -n com.webgrab.app/.MainActivity
```

## Runtime and security notes

- `GrabService` starts from the visible activity as a `dataSync` foreground service; the existing type is retained for the download/processing work rather than being relabeled to bypass platform policy. Android 15 applies a 6-hour/24-hour background runtime limit to `dataSync`; on timeout the service stops and records that the user must reopen the app. The activity checks local-server health on resume and retries a stopped/failed service. Android 16 also applies background-job quotas to `DownloadManager` work initiated from a foreground service; long or delayed downloads require real-device testing.
- The service binds only to `127.0.0.1`. Android Network Security Config denies cleartext traffic generally and permits the loopback UI endpoint. WebView only allows top-level navigation to the active local origin; other HTTP(S) links leave for the system browser.
- `allowFileAccess` is disabled, `allowContentAccess` remains enabled for system file-picker uploads, mixed content is blocked, and third-party cookies are disabled. Camera/microphone capture requests are denied because the app declares no capture feature or permissions. User-initiated downloads continue through Android `DownloadManager`.
- Backup is disabled to avoid copying app-local configuration/data; app id and SharedPreferences name remain stable for in-place upgrades.
- Downloads on Android 9 and earlier may request `WRITE_EXTERNAL_STORAGE`; Android 10+ uses `DownloadManager` scoped storage. Android 13+ may request notification permission.

## Verification status

No device compatibility is claimed by this document alone. Record each result as one of: **实机通过**, **构建/静态检查通过**, or **未验证**. A successful compile does not verify WebView navigation, Python service startup, parsing, download, conversion, notification permission, split-screen, or landscape behavior.
