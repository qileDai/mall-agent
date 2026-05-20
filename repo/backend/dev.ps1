# Start API with the project venv's Python (avoids global D:\tools\uvicorn shadowing .venv).
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "No .venv found. Run: uv sync" -ForegroundColor Red
    exit 1
}
& $Py -c "from app.serve import main; main()"
