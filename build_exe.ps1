# Build helpers
param(
    [switch]$SkipDeps,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

if (-not (Test-Path (Join-Path $Root "third_party\ffmpeg\bin\ffmpeg.exe"))) {
    throw "缺少 third_party\ffmpeg\bin\ffmpeg.exe，请先运行 tools\prepare_third_party.ps1"
}

if (-not $SkipDeps) {
    & $Py -m pip install -q --disable-pip-version-check -r (Join-Path $Root "requirements-desktop.txt")
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
}

# App icon for the exe
New-Item -ItemType Directory -Force -Path $Build | Out-Null
if (-not (Test-Path (Join-Path $Build "app.ico"))) {
    & $Py (Join-Path $Root "tools\make_icon.py")
    if ($LASTEXITCODE -ne 0) { throw "图标生成失败" }
}

if (-not $NoClean) {
    Remove-Item -Recurse -Force (Join-Path $Build "pyinstaller"), (Join-Path $Dist "WebGrab.exe") -ErrorAction SilentlyContinue
}

& $Py -m PyInstaller --noconfirm --clean `
    --distpath $Dist --workpath (Join-Path $Build "pyinstaller") `
    (Join-Path $Root "desktop\webgrab.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

$out = Join-Path $Dist "网页抓取器.exe"
Get-Process -Name "网页抓取器" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 600
if (Test-Path $out) {
    try { Remove-Item -Force $out -ErrorAction Stop }
    catch { throw "旧 exe 被占用，请先关闭正在运行的 网页抓取器.exe 再构建" }
}
Move-Item -Force (Join-Path $Dist "WebGrab.exe") $out

$h = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
$lines = @("$h  网页抓取器.exe")
$apkOut = Join-Path $Dist "网页抓取器-1.0.0-arm64.apk"
if (Test-Path $apkOut) {
    $ah = (Get-FileHash $apkOut -Algorithm SHA256).Hash.ToLower()
    $lines += "$ah  网页抓取器-1.0.0-arm64.apk"
}
[IO.File]::WriteAllText((Join-Path $Dist "SHA256SUMS.txt"), (($lines -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
"OK $out  $((Get-Item $out).Length) bytes"
"SHA256 $h"
