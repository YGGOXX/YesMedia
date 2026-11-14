param(
    [switch]$DebugBuild,
    [switch]$SkipCopy
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Android = Join-Path $Root "android"
$Dist = Join-Path $Root "dist"

function Resolve-Jdk {
    $candidates = @()
    if ($env:JAVA_HOME) { $candidates += $env:JAVA_HOME }
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "Android\Android Studio\jbr")
        foreach ($parent in @(
            (Join-Path $env:ProgramFiles "Eclipse Adoptium"),
            (Join-Path $env:ProgramFiles "Java")
        )) {
            if (Test-Path $parent) {
                $candidates += Get-ChildItem $parent -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match '(^|\D)jdk-?17' -or $_.Name -match '(^|\D)temurin-?17' } |
                    ForEach-Object { $_.FullName }
            }
        }
    }
    $javacCommand = Get-Command javac -ErrorAction SilentlyContinue
    if ($javacCommand) { $candidates += (Split-Path (Split-Path $javacCommand.Source -Parent) -Parent) }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        $java = Join-Path $candidate "bin\java.exe"
        $javac = Join-Path $candidate "bin\javac.exe"
        if ((Test-Path $java) -and (Test-Path $javac)) {
            $version = (& $java -version 2>&1 | Out-String)
            if ($version -match 'version "17(?:\.|"|\+)') { return $candidate }
        }
    }
    throw "JDK 17 was not found. Set JAVA_HOME to a JDK 17 installation."
}

function Resolve-AndroidSdk {
    $candidates = @()
    if ($env:ANDROID_SDK_ROOT) { $candidates += $env:ANDROID_SDK_ROOT }
    if ($env:ANDROID_HOME) { $candidates += $env:ANDROID_HOME }
    $localProps = Join-Path $Android "local.properties"
    if (Test-Path $localProps) {
        $line = Get-Content $localProps | Where-Object { $_ -match '^\s*sdk\.dir\s*=' } | Select-Object -First 1
        if ($line) { $candidates += (($line -split '=', 2)[1].Trim() -replace '\\\\', '\') }
    }
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Android\Sdk") }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if ((Test-Path (Join-Path $candidate "platforms\android-35")) -and
            (Test-Path (Join-Path $candidate "platform-tools\adb.exe")) -and
            (Get-ChildItem (Join-Path $candidate "build-tools") -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path (Join-Path $_.FullName "aapt2.exe") })) {
            return $candidate
        }
    }
    throw "Android SDK is incomplete. Install platform android-35, Build Tools (aapt2), and Platform Tools; set ANDROID_SDK_ROOT or ANDROID_HOME."
}

$Jdk = Resolve-Jdk
$Sdk = Resolve-AndroidSdk
$env:JAVA_HOME = $Jdk
$env:ANDROID_HOME = $Sdk
$env:ANDROID_SDK_ROOT = $Sdk
$env:Path = "$(Join-Path $Jdk 'bin');$(Join-Path $Sdk 'platform-tools');$env:Path"

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw "Python 3.13 is required for Chaquopy. Install Python 3.13 or create .venv. " }
    $pyVersion = (& $python.Source --version 2>&1 | Out-String).Trim()
    if ($pyVersion -notmatch '^Python 3\.13(?:\.|$)') { throw "Chaquopy requires Python 3.13; found $pyVersion. Create .venv with Python 3.13." }
    & $python.Source -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Failed to create project-local Python 3.13 virtual environment." }
}
if ((& $Py --version 2>&1 | Out-String) -notmatch 'Python 3\.13(?:\.|$)') { throw "The project .venv must use Python 3.13." }
$env:WEBGRAB_BUILD_PYTHON = $Py

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
Push-Location $Android
try {
    if (-not $SkipCopy) {
        & (Join-Path $Android "copy_python.ps1")
        if (-not $?) { throw "copy_python failed." }
    }
    & $Py (Join-Path $Root "tools\check_python_sync.py")
    if ($LASTEXITCODE -ne 0) { throw "core/webgrab and generated Android Python sources differ." }
    $task = if ($DebugBuild) { "assembleDebug" } else { "assembleRelease" }
    & (Join-Path $Android "gradlew.bat") $task --no-daemon
    if ($LASTEXITCODE -ne 0) { throw "Gradle $task failed." }
} finally {
    Pop-Location
}

$variant = if ($DebugBuild) { "debug" } else { "release" }
$apk = Get-ChildItem (Join-Path $Android "app\build\outputs\apk\$variant") -Filter "*.apk" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $apk) { throw "Gradle completed but no APK was found in the $variant output directory." }

$suffix = if ($DebugBuild) { "debug" } else { "release" }
$out = Join-Path $Dist "YesMedia-1.0.1-$suffix.apk"
Copy-Item -Force $apk.FullName $out
$hash = (Get-FileHash $out -Algorithm SHA256).Hash.ToLowerInvariant()
$hashPath = Join-Path $Dist "YesMedia-1.0.1-$suffix.sha256"
[IO.File]::WriteAllText($hashPath, "$hash  $([IO.Path]::GetFileName($out))`n", [Text.UTF8Encoding]::new($false))
"OK $out  $((Get-Item $out).Length) bytes"
"SHA256 $hash"

$aapt2 = Get-ChildItem (Join-Path $Sdk "build-tools") -Directory |
    Sort-Object { [version]$_.Name } -Descending |
    ForEach-Object { Join-Path $_.FullName "aapt2.exe" } |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($aapt2) {
    & $aapt2 dump badging $out | Select-String "package:|sdkVersion|targetSdkVersion|native-code|uses-permission"
}
