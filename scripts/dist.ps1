[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Remove-BuildArtifact {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $Resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not $Resolved.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside repo: $Resolved"
    }

    Remove-Item -LiteralPath $Resolved -Recurse -Force
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

if (-not $SkipTests) {
    Invoke-Native python -m pytest
}

Invoke-Native python -m pip install --upgrade build

Remove-BuildArtifact (Join-Path $RepoRoot "dist")
Remove-BuildArtifact (Join-Path $RepoRoot "build")

Get-ChildItem -LiteralPath $RepoRoot -Directory -Filter "*.egg-info" | ForEach-Object {
    Remove-BuildArtifact $_.FullName
}

Invoke-Native python -m build --wheel

Remove-BuildArtifact (Join-Path $RepoRoot "build")
Get-ChildItem -LiteralPath $RepoRoot -Directory -Filter "*.egg-info" | ForEach-Object {
    Remove-BuildArtifact $_.FullName
}

$Wheel = Get-ChildItem -LiteralPath (Join-Path $RepoRoot "dist") -Filter "*.whl" | Select-Object -First 1
if ($null -eq $Wheel) {
    throw "Wheel build did not produce a .whl file"
}

$BundleRoot = Join-Path $RepoRoot "dist\dantebridge-tester"
$BundleApp = Join-Path $BundleRoot "app"
Remove-BuildArtifact $BundleRoot
New-Item -ItemType Directory -Path $BundleApp | Out-Null

Copy-Item -LiteralPath $Wheel.FullName -Destination $BundleApp
Copy-Item -LiteralPath (Join-Path $RepoRoot "config\example.endpoint.toml") -Destination $BundleApp

@'
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-dantebridge.ps1"
'@ | Set-Content -LiteralPath (Join-Path $BundleRoot "Run Dante Bridge.cmd") -Encoding ASCII

@'
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$AppDir = Join-Path $Root "app"
$VenvDir = Join-Path $Root ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    py -3 -m venv $VenvDir
}

& $Python -m pip install --upgrade pip
& $Python -m pip install --force-reinstall (Get-ChildItem -LiteralPath $AppDir -Filter "*.whl" | Select-Object -First 1).FullName

Write-Host "Starting Dante Bridge at http://127.0.0.1:8443"
Start-Process "http://127.0.0.1:8443"
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8443
'@ | Set-Content -LiteralPath (Join-Path $BundleRoot "run-dantebridge.ps1") -Encoding ASCII

@'
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$ROOT/app"
VENV_DIR="$ROOT/.venv"
PYTHON="$VENV_DIR/bin/python"

if [ ! -x "$PYTHON" ]; then
  python3 -m venv "$VENV_DIR"
fi

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install --force-reinstall "$APP_DIR"/*.whl

echo "Starting Dante Bridge at http://127.0.0.1:8443"
if command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:8443"
fi
"$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8443
'@ | Set-Content -LiteralPath (Join-Path $BundleRoot "run-dantebridge.command") -Encoding ASCII

@'
# Dante Bridge Tester Build

## Requirements

- Python 3.11 or newer.
- GStreamer 1.22+ with `gst-launch-1.0` and `gst-inspect-1.0` on `PATH`.
- GStreamer plugins for SRT, Opus, MPEG-TS, audio conversion/resampling, audio test sources, and your host audio backend.
- Dante Virtual Soundcard in ASIO mode only, plus Dante Controller for Dante I/O testing.

## Windows

Double-click `Run Dante Bridge.cmd`.

## macOS

Open Terminal in this folder and run:

```sh
chmod +x run-dantebridge.command
./run-dantebridge.command
```

The app opens at `http://127.0.0.1:8443`.

This tester build creates a local `.venv` beside the launcher on first run.
'@ | Set-Content -LiteralPath (Join-Path $BundleRoot "README.md") -Encoding ASCII

$ZipPath = Join-Path $RepoRoot "dist\dantebridge-tester.zip"
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $BundleRoot "*") -DestinationPath $ZipPath -Force

Get-ChildItem -LiteralPath (Join-Path $RepoRoot "dist")
