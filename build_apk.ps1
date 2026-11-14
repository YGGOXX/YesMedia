param(
    [switch]$DebugBuild,
    [switch]$SkipCopy
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Android = Join-Path $Root "android"
$Dist = Join-Path $Root "dist"
$Jdk = "G:\tools\jdk17"
$Sdk = "G:\tools\android-sdk"
$Gradle = "G:\tools\gradle-8.13"

if (-not (Test-Path "$Jdk\bin\java.exe")) { throw "缺少 JDK17: $Jdk" }
if (-not (Test-Path "$Sdk\platform-tools")) { throw "缺少 Android SDK: $Sdk" }
$env:JAVA_HOME = $Jdk
$env:ANDROID_HOME = $Sdk
$env:ANDROID_SDK_ROOT = $Sdk
$env:Path = "$Jdk\bin;$env:Path"

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
Push-Location $Android
try {
    if (-not $SkipCopy) {
        & (Join-Path $Android "copy_python.ps1")
        if (-not $?) { throw "copy_python 失败" }
    }
    $Py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $Py)) { throw "缺少 .venv Python: $Py" }
    & $Py (Join-Path $Root "tools\check_python_sync.py")
    if ($LASTEXITCODE -ne 0) { throw "core/webgrab 与 Android 生成副本不一致，已停止构建" }
    $task = if ($DebugBuild) { "assembleDebug" } else { "assembleRelease" }
    & (Join-Path $Android "gradlew.bat") $task --no-daemon
    if ($LASTEXITCODE -ne 0) { throw "Gradle $task 失败" }
} finally {
    Pop-Location
}

$variant = if ($DebugBuild) { "debug" } else { "release" }
$apk = Get-ChildItem (Join-Path $Android "app\build\outputs\apk\$variant") -Filter "*.apk" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $apk) { throw "找不到 APK 产物" }

$out = Join-Path $Dist "YesMedia-1.0.0-arm64.apk"
Copy-Item -Force $apk.FullName $out
$h = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
$lines = @("$h  YesMedia-1.0.0-arm64.apk")
[IO.File]::WriteAllText((Join-Path $Dist "SHA256SUMS.txt"), (($lines -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
"OK $out  $((Get-Item $out).Length) bytes"
"SHA256 $h"

$aapt = Join-Path $Sdk "build-tools\35.0.0\aapt2.exe"
if (Test-Path $aapt) {
    & $aapt dump badging $out | Select-String "package:|sdkVersion|targetSdkVersion|native-code|uses-permission" | Select-Object -First 20
}
