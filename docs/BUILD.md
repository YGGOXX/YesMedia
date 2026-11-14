# YesMedia 1.0.1 Build Guide

This guide describes repository build inputs, not a claim that a particular workstation, APK, or device has passed verification.

## Requirements

- Windows PowerShell (the provided `build_apk.ps1` is PowerShell)
- JDK 17
- Android SDK platform `android-35`, Build Tools with `aapt2`, and Platform Tools
- Python 3.13 (the script creates the project `.venv` if needed)
- Network access for Gradle, Maven and Chaquopy build dependencies

`build_apk.ps1` detects JDK and SDK from `JAVA_HOME`, `ANDROID_SDK_ROOT`, `ANDROID_HOME`, `android/local.properties`, and standard Windows installation locations. Set `JAVA_HOME` and/or `ANDROID_SDK_ROOT` explicitly when detection is ambiguous. Builds use the checked-in Gradle Wrapper (8.13); installing a separate Gradle distribution is not required.

## Build

Run from repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_apk.ps1 -DebugBuild
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_apk.ps1
```

The first command produces `dist/YesMedia-1.0.1-debug.apk`; the second produces `dist/YesMedia-1.0.1-release.apk`. Each has a separate `.sha256` sidecar. A Debug build uses Android's debug key and never overwrites the Release artifact.

Before Gradle runs, `copy_python.ps1` synchronizes `core/webgrab` and static UI files into generated Android source/assets and `tools/check_python_sync.py` checks the Python copy. Do not hand-edit generated copies.

## Release signing

Release signing credentials must be supplied privately through environment variables or user-level Gradle properties (`~/.gradle/gradle.properties`):

```properties
WEBGRAB_STORE_FILE=C:/private/keys/yesmedia-release.jks
WEBGRAB_STORE_PASSWORD=<private value>
WEBGRAB_KEY_ALIAS=<private alias>
WEBGRAB_KEY_PASSWORD=<private value>
```

No real values belong in this repository. If release signing is absent or incomplete, `assembleRelease` fails with an explicit diagnostic; it does not quietly produce an unsigned APK. Never reuse credentials exposed in repository history; removing them from the current tree does not erase old commits.

## Automated checks

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe tools\smoke_test.py
```

The Android build script also runs the generated-source sync check. It creates `.venv` using Python 3.13 when needed but does not install desktop test dependencies automatically.

Build a Debug APK from repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_apk.ps1 -DebugBuild
```

For direct Gradle use, first run `android\copy_python.ps1` and `tools\check_python_sync.py`, then from the `android` directory run:

```powershell
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug --no-daemon
```

When Python 3.13 is installed under a different executable name, create `.venv` with that interpreter before building. Optional UI tests require their documented browser/runtime setup and should be reported separately.

## APK and device checks

After a successful build, inspect metadata and ABI contents before installing:

```powershell
$apk = '..\dist\YesMedia-1.0.1-debug.apk'
& "$env:ANDROID_SDK_ROOT\build-tools\35.0.0\aapt2.exe" dump badging $apk
adb devices -l
adb install -r $apk
adb shell am start -n com.webgrab.app/.MainActivity
adb logcat -s YesMedia AndroidRuntime
```

The build requests `arm64-v8a` and `x86_64`. Confirm the APK contains the matching Chaquopy and FFmpeg native libraries (and no missing native dependency) before claiming either architecture. Emulator, tablet, landscape, split-window, WebView, real-site parsing, downloads, conversions, permissions, and foreground-service timeout behavior require separate runtime verification; do not infer those from Gradle success.

## Android service lifecycle

The app targets Android 15 (API 35) and declares `dataSync` because the service hosts the local Python server and supports downloads/conversion. Android 15 permits this type up to six hours in the background over 24 hours and invokes `Service.onTimeout`; the implementation stops rather than changing the service type. Reopen the app after timeout to restart the service. On Android 16, jobs started from a foreground service, including `DownloadManager`, must obey their job quotas. These limits mean uninterrupted long-running downloads are not guaranteed and must be tested on the actual OS version.
