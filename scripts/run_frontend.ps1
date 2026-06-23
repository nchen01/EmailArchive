<#
.SYNOPSIS
    Start the EKC frontend dev server on a deterministic port (S10).

.DESCRIPTION
    Launches Vite on a fixed port (5173). vite.config.ts sets strictPort=true,
    so if 5173 is already in use the dev server FAILS LOUDLY instead of silently
    moving to 5174. If that happens, Ctrl+C the old `npm run dev` window (or this
    script's previous run) and start again -- the URL is always the same:

        http://localhost:5173

    The mailbox id is passed to the app via VITE_MAILBOX_ID so the UI loads a
    mailbox without manual entry.

.PARAMETER MailboxId
    Mailbox UUID for the UI. Defaults to $env:VITE_MAILBOX_ID, then
    $env:EKC_MAILBOX_ID.

.PARAMETER Port
    Dev server port. Default 5173. Must match vite.config.ts strictPort.

.EXAMPLE
    .\scripts\run_frontend.ps1
.EXAMPLE
    .\scripts\run_frontend.ps1 -MailboxId e21c187a-956a-47ee-92aa-b21badd16f4d
#>
[CmdletBinding()]
param(
    [string]$MailboxId = $(if ($env:VITE_MAILBOX_ID) { $env:VITE_MAILBOX_ID } else { $env:EKC_MAILBOX_ID }),
    [int]$Port = 5173
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $RepoRoot "frontend"
Set-Location $RepoRoot

Write-Host "== EKC frontend launcher ==" -ForegroundColor Cyan
Write-Host "repo    : $RepoRoot"
Write-Host "url     : http://localhost:$Port"

if ($MailboxId) {
    Write-Host "mailbox : $MailboxId"
} else {
    Write-Host "mailbox : (none -- set EKC_MAILBOX_ID/VITE_MAILBOX_ID or enter it in the UI)" -ForegroundColor Yellow
}

# Ensure node_modules exists; install once if missing.
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "node_modules missing -- running npm install (one time)..." -ForegroundColor Yellow
    npm.cmd --prefix $FrontendDir install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Aborting: npm install failed." -ForegroundColor Red
        exit 1
    }
}

# Pass mailbox id to Vite via env so import.meta.env.VITE_MAILBOX_ID is set.
$env:VITE_MAILBOX_ID = $MailboxId

Write-Host ""
Write-Host "-- starting Vite (strictPort: will fail if $Port is busy) --" -ForegroundColor Cyan
Write-Host "   If it fails with 'Port $Port is in use', Ctrl+C the old frontend window first." -ForegroundColor Yellow
npm.cmd --prefix $FrontendDir run dev -- --port $Port --strictPort
