<#
.SYNOPSIS
    Start the EKC FastAPI backend with the blessed venv Python (S10).

.DESCRIPTION
    A single deterministic backend launcher. It:
      1. cd's to the repo root (independent of the caller's CWD).
      2. Verifies <repo>\.venv\Scripts\python.exe and required imports
         (via check_local_env.ps1).
      3. Runs the operational preflight (services/preflight.py). If a mailbox id
         is available it also verifies embeddings for that mailbox.
      4. Starts uvicorn ONLY if preflight is acceptable (override with -Force).

    .env is loaded by services/api/main.py at startup, so VOYAGE_API_KEY /
    ANTHROPIC_API_KEY / DATABASE_URL come from .env automatically.

    No bare `python` is ever used.

.PARAMETER MailboxId
    Mailbox UUID to verify in preflight. Defaults to $env:EKC_MAILBOX_ID.

.PARAMETER Port
    Port for uvicorn. Default 8000 (the port the Vite proxy forwards to).

.PARAMETER Force
    Start uvicorn even if preflight reports a failure.

.PARAMETER SkipPreflight
    Skip the preflight step entirely (still runs the env check).

.PARAMETER NoReload
    Disable uvicorn --reload (use for a stable, non-watching process).

.EXAMPLE
    .\scripts\run_backend.ps1
.EXAMPLE
    .\scripts\run_backend.ps1 -MailboxId e21c187a-956a-47ee-92aa-b21badd16f4d
#>
[CmdletBinding()]
param(
    [string]$MailboxId = $env:EKC_MAILBOX_ID,
    [int]$Port = 8000,
    [switch]$Force,
    [switch]$SkipPreflight,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
Set-Location $RepoRoot

Write-Host "== EKC backend launcher ==" -ForegroundColor Cyan
Write-Host "repo   : $RepoRoot"
Write-Host "python : $VenvPython"

# 1. Environment validation (fails loudly if venv/imports are broken).
& (Join-Path $PSScriptRoot "check_local_env.ps1") -Quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "Aborting: local environment check failed. Run scripts\check_local_env.ps1 for detail." -ForegroundColor Red
    exit 1
}

# 2. Preflight (operational readiness). Gate uvicorn on the result.
if (-not $SkipPreflight) {
    Write-Host ""
    Write-Host "-- preflight --" -ForegroundColor Cyan
    if ($MailboxId) {
        Write-Host "mailbox: $MailboxId"
        & $VenvPython -m scripts.preflight --mailbox-id $MailboxId
    } else {
        Write-Host "mailbox: (none -- set EKC_MAILBOX_ID or pass -MailboxId to verify embeddings)" -ForegroundColor Yellow
        & $VenvPython -m scripts.preflight
    }
    $preflightExit = $LASTEXITCODE
    if ($preflightExit -ne 0) {
        if ($Force) {
            Write-Host "Preflight failed but -Force was given -- starting backend anyway." -ForegroundColor Yellow
        } else {
            Write-Host ""
            Write-Host "Aborting: preflight failed. Fix the issues above, or re-run with -Force to start anyway." -ForegroundColor Red
            exit 1
        }
    }
}

# 3. Start uvicorn with the blessed interpreter.
Write-Host ""
Write-Host "-- starting uvicorn on http://127.0.0.1:$Port --" -ForegroundColor Cyan
$uvArgs = @("-m", "uvicorn", "services.api.main:app", "--host", "127.0.0.1", "--port", "$Port")
if (-not $NoReload) { $uvArgs += "--reload" }
& $VenvPython @uvArgs
