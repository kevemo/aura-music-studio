$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Provider = if ($env:LSS_SETUP_PROVIDER) { $env:LSS_SETUP_PROVIDER } else { "direct" }
$HostnameValue = $env:LSS_SETUP_HOSTNAME
$DuckSub = $env:LSS_SETUP_DUCKDNS_SUBDOMAIN

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required to launch the self-hosted Command Center stack."
}

if (-not (Test-Path ".env")) {
    $Args = @("scripts/setup_self_host.py", "--provider", $Provider)
    if ($HostnameValue) { $Args += @("--hostname", $HostnameValue) }
    if ($DuckSub) { $Args += @("--duckdns-subdomain", $DuckSub) }
    python @Args
    Write-Host ""
    Write-Host "Review .env and add any private DDNS/SMTP credentials shown as missing, then run this script again."
    exit 0
}

$EnvText = Get-Content ".env" -Raw
if ($EnvText -match "(?m)^LSS_DDNS_PROVIDER=(direct|freedns|duckdns)$") {
    docker compose --profile public up -d --build
} else {
    docker compose up -d --build
}

Write-Host ""
Write-Host "Elevate Souls Productions Content Creation Command Center started. Local owner access: http://127.0.0.1:8000"
Write-Host "Run: docker compose exec live-sound-studio aura public-address --refresh"
