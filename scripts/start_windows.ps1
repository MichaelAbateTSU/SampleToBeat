<#
.SYNOPSIS
  Start ChopLab AI on Windows and open it in the browser.
.PARAMETER Dev
  Run the Vite dev server (hot reload, http://localhost:5173) alongside the backend instead of the production build.
.PARAMETER Port
  Backend port (default 8765, or $env:CHOPLAB_PORT).
.PARAMETER NoBrowser
  Don't open a browser window.
#>
param([switch]$Dev, [int]$Port = $(if ($env:CHOPLAB_PORT) { [int]$env:CHOPLAB_PORT } else { 8765 }), [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { $env:Path = "$env:USERPROFILE\.local\bin;$env:Path" }
if (-not (Test-Path "$Root\backend\.venv")) { throw "Not set up yet - run scripts\setup_windows.ps1 first." }
$env:CHOPLAB_PORT = "$Port"
$vite = $null

if ($Dev) {
  $vite = Start-Process -PassThru -WorkingDirectory "$Root\frontend" -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev"
  $url = "http://localhost:5173"
} else {
  if (-not (Test-Path "$Root\frontend\dist\index.html")) {
    Write-Host "Building frontend..."
    Push-Location "$Root\frontend"
    try { npm run build; if ($LASTEXITCODE -ne 0) { throw "frontend build failed" } } finally { Pop-Location }
  }
  $url = "http://127.0.0.1:$Port"
}

if (-not $NoBrowser) {
  # open the browser once the API answers
  Start-Job -ArgumentList $url, $Port -ScriptBlock {
    param($url, $port)
    for ($i = 0; $i -lt 120; $i++) {
      try { Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 1 | Out-Null; Start-Process $url; return }
      catch { Start-Sleep -Milliseconds 500 }
    }
  } | Out-Null
}

Write-Host "ChopLab AI -> $url   (Ctrl+C to stop)" -ForegroundColor Green
Push-Location "$Root\backend"
try { uv run choplab }
finally {
  Pop-Location
  if ($vite) { Stop-Process -Id $vite.Id -ErrorAction SilentlyContinue }
}
