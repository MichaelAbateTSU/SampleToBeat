<#
.SYNOPSIS
  One-time setup for ChopLab AI on Windows: backend (uv + Python 3.12), frontend (npm), production build.
.PARAMETER Separation
  Also install the optional Demucs / audio-separator stack (large download, CPU inference is slow).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
#>
param([switch]$Separation)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Step "Checking uv"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "uv not found - installing it (https://docs.astral.sh/uv/)"
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv --version

Step "Checking Node.js (20+)"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  throw "Node.js is not installed. Install the LTS version from https://nodejs.org (or: winget install OpenJS.NodeJS.LTS), then rerun."
}
$nodeMajor = [int]((node --version).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 20) { throw "Node.js 20+ required (found $(node --version))." }
node --version

Step "Installing backend (uv sync - downloads Python 3.12 if needed)"
Push-Location "$Root\backend"
try {
  if ($Separation) { uv sync --extra separation } else { uv sync }
  if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
} finally { Pop-Location }

Step "Installing frontend (npm ci)"
Push-Location "$Root\frontend"
try {
  npm ci --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  Step "Building frontend"
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
} finally { Pop-Location }

Step "Environment check"
Push-Location "$Root\backend"
try { uv run --no-sync python "$Root\scripts\check_environment.py" } finally { Pop-Location }

Write-Host "`nSetup complete. Start ChopLab with:  powershell -ExecutionPolicy Bypass -File scripts\start_windows.ps1" -ForegroundColor Green
