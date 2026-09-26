# Start Sensei API for Pagoda Pro local UI work.
# Run from repo root:  powershell -File scripts\start-sensei.ps1
# Pro .env.local:      SENSEI_API_URL=http://127.0.0.1:8000

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Creating venv..."
    python -m venv venv
    .\venv\Scripts\python.exe -m pip install -r requirements.txt
}

$env:USE_MOCK_TOURS = "true"
if (-not $env:SUPABASE_URL) { $env:SUPABASE_URL = "https://mock.supabase.local" }
if (-not $env:SUPABASE_ANON_KEY) { $env:SUPABASE_ANON_KEY = "mock-anon-key" }

Write-Host "Sensei API  http://127.0.0.1:8000"
Write-Host "Docs        http://127.0.0.1:8000/docs"
Write-Host "Recommend   POST /sensei/recommend"
Write-Host "Guide tours GET  /sensei/guide-tours"
Write-Host ""

.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
