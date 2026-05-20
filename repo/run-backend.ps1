# 从仓库根目录启动后端（自动进入 backend 并使用 .venv 里的 Python）。
# 用法：在 D:\project\mall-agent 下执行 .\run-backend.ps1
$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$Backend = Join-Path $RepoRoot "backend"
if (-not (Test-Path $Backend)) {
    Write-Error "未找到 backend 目录: $Backend"
    exit 1
}
Set-Location $Backend
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "未找到 .venv，请先执行: cd backend; uv sync" -ForegroundColor Yellow
    exit 1
}
Write-Host "工作目录: $Backend" -ForegroundColor Cyan
Write-Host "Python:   $Py" -ForegroundColor Cyan
& $Py -c "from app.serve import main; main()"
