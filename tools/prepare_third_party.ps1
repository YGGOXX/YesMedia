# Extract bundled third-party tools (Windows)
param(
    [string]$FfmpegZip = "G:\tools\downloads\ffmpeg.zip"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tp = Join-Path $Root "third_party"
$Ff = Join-Path $Tp "ffmpeg\bin"
$Tmp = Join-Path $Tp "_unzip"

if (-not (Test-Path $FfmpegZip)) { throw "找不到 $FfmpegZip" }
if (Test-Path $Ff) { Remove-Item -Recurse -Force $Ff }
if (Test-Path $Tmp) { Remove-Item -Recurse -Force $Tmp }
New-Item -ItemType Directory -Force -Path $Ff, $Tmp | Out-Null

Expand-Archive -Path $FfmpegZip -DestinationPath $Tmp -Force
$bin = Get-ChildItem $Tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
if (-not $bin) { throw "zip 里没有 ffmpeg.exe" }
Copy-Item $bin.FullName (Join-Path $Ff "ffmpeg.exe")
# 不带 ffprobe：桌面 media.py 用 ffmpeg 输出探测音轨（见 docs/CONTRACT.md）
$lic = Get-ChildItem $Tmp -Recurse -Include "LICENSE*","COPYING*" | Select-Object -First 1
if ($lic) { Copy-Item $lic.FullName (Join-Path $Tp "LICENSE-ffmpeg.txt") }
Remove-Item -Recurse -Force $Tmp

& (Join-Path $Ff "ffmpeg.exe") -version | Select-Object -First 1
"OK $Ff"
