$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$AceRoot = Join-Path $RepoRoot "engines\ace-step-1.5"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it first, then rerun this script."
}
if (-not (Test-Path $AceRoot)) {
    throw "ACE-Step 1.5 is not cloned at $AceRoot. Run: aura engines --bootstrap"
}

Write-Host "Aura Music Studio — starting ACE-Step 1.5 REST API" -ForegroundColor Cyan
Write-Host "Engine: $AceRoot"
Set-Location $AceRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Preparing ACE-Step environment with uv sync..." -ForegroundColor Yellow
    uv sync
}

if ($env:ACESTEP_API_KEY) {
    Write-Host "ACE-Step API authentication is enabled." -ForegroundColor Green
    uv run acestep-api --api-key $env:ACESTEP_API_KEY
} else {
    Write-Host "Starting local API without an API key. Keep this bound to a trusted machine/network." -ForegroundColor Yellow
    uv run acestep-api
}
