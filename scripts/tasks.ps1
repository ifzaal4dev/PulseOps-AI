# PulseOps task runner for Windows. Usage: .\scripts\tasks.ps1 <target>
# Targets match the Makefile.
param([Parameter(Mandatory = $true)][string]$Target)

$Root = Split-Path -Parent $PSScriptRoot

switch ($Target) {
    "install"     { & $PSCommandPath install-ml; & $PSCommandPath install-api; & $PSCommandPath install-web }
    "install-ml"  { Set-Location $Root; python -m pip install -e "ml[dev]" }
    "install-api" { Set-Location "$Root\apps\api"; python -m pip install -e ".[dev]" }
    "install-web" { Set-Location "$Root\apps\web"; npm install }
    "reset"       { Set-Location "$Root\apps\api"; python -m pulseops_api.init_db --reseed }
    "api"         { Set-Location "$Root\apps\api"; python -m uvicorn pulseops_api.main:app --host 127.0.0.1 --port 8000 --reload }
    "web"         { Set-Location "$Root\apps\web"; npm run dev }
    "test"        { & $PSCommandPath test-api; & $PSCommandPath test-ml }
    "test-api"    { Set-Location "$Root\apps\api"; python -m pytest tests/ -q }
    "test-ml"     { Set-Location $Root; python -m pytest ml/tests -q }
    "eval"        { Set-Location $Root; python eval/run_eval.py }
    "lint"        { Set-Location $Root; python -m ruff check .; python -m ruff format --check .; Set-Location "$Root\apps\web"; npm run lint; npm run format:check }
    "format"      { Set-Location $Root; python -m ruff format .; Set-Location "$Root\apps\web"; npm run format }
    "demo"        { Set-Location "$Root\apps\api"; python -m pulseops_api.demo_golden_path }
    "clean"       { Remove-Item "$Root\apps\api\pulseops.db" -ErrorAction SilentlyContinue }
    default       { Write-Error "Unknown target: $Target" }
}
