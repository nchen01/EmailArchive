<#
.SYNOPSIS
    Validate the blessed local runtime for Email Knowledge Continuity (S10).

.DESCRIPTION
    One blessed Python runtime is supported for local dev:

        <repo>\.venv\Scripts\python.exe

    This script verifies that runtime is present and can import every backend
    module the API needs -- including the Voyage embed client, which must NOT
    drag in the voyageai SDK / langchain / uuid_utils native chain (that chain
    is blocked by Windows Application Control and was crashing the runtime).

    It fails loudly with an actionable message when anything is missing, so an
    operator never has to guess why the stack will not start, and never has to
    fall back to a bare `python` on PATH.

    Exit code 0 = environment OK; 1 = a required check failed.

.PARAMETER Quiet
    Suppress per-check "[ok]" lines; only print failures and the summary.
#>
[CmdletBinding()]
param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

# Repo root is the parent of this script's directory, regardless of caller CWD.
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

function Write-Ok($msg)   { if (-not $Quiet) { Write-Host "[ok]   $msg" -ForegroundColor Green } }
function Write-Bad($msg)  { Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Write-Warn2($msg) { Write-Host "[warn] $msg" -ForegroundColor Yellow }

$failed = $false

# 1. Blessed venv Python must exist.
if (Test-Path $VenvPython) {
    $ver = & $VenvPython --version
    Write-Ok "venv Python found: $VenvPython ($ver)"
} else {
    Write-Bad "venv Python NOT found at $VenvPython"
    Write-Host "       Create it with:  python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -e .[dev]" -ForegroundColor Red
    # Without the interpreter nothing else can run -- abort immediately.
    exit 1
}

# 2. Required backend imports must succeed in THIS interpreter.
#    embed_client is included specifically to catch the native-dependency
#    regression: importing it must never pull in voyageai/uuid_utils.
$importProbe = @"
import sys
mods = ['sqlalchemy', 'uvicorn', 'fastapi', 'dotenv', 'anthropic', 'pgvector']
missing = []
for m in mods:
    try:
        __import__(m)
    except Exception as exc:
        missing.append(m + ' (' + type(exc).__name__ + ')')
from services.retrieval.embed_client import VoyageEmbedClient  # must not import voyageai
forbidden = {'voyageai','langchain','langchain_core','langchain_text_splitters','uuid_utils'}
leaked = sorted(forbidden & {x.split('.')[0] for x in sys.modules})
if missing:
    print('MISSING:' + ', '.join(missing)); sys.exit(2)
if leaked:
    print('LEAKED:' + ', '.join(leaked)); sys.exit(3)
print('IMPORTS_OK')
"@

$probeOut = & $VenvPython -c $importProbe
if ($LASTEXITCODE -eq 0) {
    Write-Ok "backend imports OK (embed client clean of voyageai/uuid_utils)"
} elseif ($probeOut -like "MISSING:*") {
    Write-Bad "missing backend dependencies: $($probeOut -replace '^MISSING:','')"
    Write-Host "       Fix with:  .\.venv\Scripts\python.exe -m pip install -e .[dev]" -ForegroundColor Red
    $failed = $true
} elseif ($probeOut -like "LEAKED:*") {
    Write-Bad "embed client pulled in forbidden native chain: $($probeOut -replace '^LEAKED:','')"
    Write-Host "       The Voyage embed client must use the HTTP path only (no voyageai SDK)." -ForegroundColor Red
    $failed = $true
} else {
    Write-Bad "import probe failed: $probeOut"
    $failed = $true
}

# 3. .env presence (warn-only -- process env can supply the same values).
$envFile = Join-Path $RepoRoot ".env"
if (Test-Path $envFile) {
    Write-Ok ".env present"
} else {
    Write-Warn2 ".env not found at $envFile -- keys/DATABASE_URL must come from the process environment"
}

Write-Host ""
if ($failed) {
    Write-Host "Local environment check FAILED -- fix the issues above." -ForegroundColor Red
    exit 1
} else {
    Write-Host "Local environment OK." -ForegroundColor Green
    exit 0
}
