# Works with Windows PowerShell 5.1 and PowerShell 7+.
<#
.SYNOPSIS
    Copies the Python sources of core/webgrab into the Android module.
    Run once before every build.

.DESCRIPTION
    - core/webgrab/*.py (including sub packages) --> app/src/main/python/webgrab
    - core/webgrab/static/*                       --> app/src/main/assets/webgrab-static
                                                  --> app/src/main/python/webgrab/static
    - __pycache__ and static are skipped for the python package tree.

    Both targets are build outputs. Do not edit them by hand: the next sync
    overwrites them.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File android/copy_python.ps1
    powershell -ExecutionPolicy Bypass -File android/copy_python.ps1 -Clean
#>
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$androidDir = $PSScriptRoot
$projectRoot = Split-Path -Parent $androidDir
$coreDir = Join-Path $projectRoot 'core\webgrab'
$pyDst = Join-Path $androidDir 'app\src\main\python\webgrab'
$assetDst = Join-Path $androidDir 'app\src\main\assets\webgrab-static'

if (-not (Test-Path (Join-Path $coreDir '__init__.py'))) {
    throw "core/webgrab not found: $coreDir"
}

function Get-RelativePath([string]$baseDir, [string]$fullPath) {
    $base = $baseDir.TrimEnd('\')
    if (-not $fullPath.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) {
        throw "path '$fullPath' is not under '$baseDir'"
    }
    return $fullPath.Substring($base.Length).TrimStart('\')
}

if ($Clean) {
    foreach ($dir in @($pyDst, $assetDst)) {
        if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
    }
}

New-Item -ItemType Directory -Force -Path $pyDst | Out-Null
New-Item -ItemType Directory -Force -Path $assetDst | Out-Null

$dirs = Get-ChildItem $coreDir -Directory -Recurse | Where-Object {
    $_.FullName -notmatch '\\static(\\|$)' -and $_.FullName -notmatch '__pycache__'
}
foreach ($d in $dirs) {
    $rel = Get-RelativePath $coreDir $d.FullName
    New-Item -ItemType Directory -Force -Path (Join-Path $pyDst $rel) | Out-Null
}

$copied = 0
$files = Get-ChildItem $coreDir -File -Recurse | Where-Object {
    $_.Extension -eq '.py' -and $_.FullName -notmatch '__pycache__'
}
foreach ($f in $files) {
    $rel = Get-RelativePath $coreDir $f.FullName
    $dest = Join-Path $pyDst $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Copy-Item $f.FullName $dest -Force
    $copied++
}

$staticSrc = Join-Path $coreDir 'static'
$assetFiles = 0
if (Test-Path $staticSrc) {
    Copy-Item (Join-Path $staticSrc '*') $assetDst -Recurse -Force
    $assetFiles = (Get-ChildItem $staticSrc -File -Recurse).Count

    $pyStatic = Join-Path $pyDst 'static'
    New-Item -ItemType Directory -Force -Path $pyStatic | Out-Null
    Copy-Item (Join-Path $staticSrc '*') $pyStatic -Recurse -Force
}

Write-Host ("copy_python: {0} .py files -> {1}" -f $copied, $pyDst) -ForegroundColor Green
Write-Host ("copy_python: {0} static files -> {1}" -f $assetFiles, $assetDst) -ForegroundColor Green
