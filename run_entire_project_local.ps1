# ==============================================================================
# KYC Behavioral Observability Framework -- Full Pipeline PowerShell Launcher
# Student: Pranali Pandharinath Supekar (2024DA04387)
# ==============================================================================

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  KYC BEHAVIORAL OBSERVABILITY FRAMEWORK -- FULL PIPELINE LOCAL EXECUTION" -ForegroundColor Green
Write-Host "==============================================================================" -ForegroundColor Cyan

Set-Location $PSScriptRoot

Write-Host "`n[*] Step 0: Verifying Docker Infrastructure..." -ForegroundColor Yellow
docker compose up -d
docker ps

Write-Host "`n[*] Launching Master Pipeline Runner..." -ForegroundColor Yellow
& "$PSScriptRoot\venv\Scripts\python.exe" "$PSScriptRoot\run_all_pipeline.py"

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n==============================================================================" -ForegroundColor Cyan
    Write-Host "  [+] ALL PIPELINE STAGES, TESTS & GATES COMPLETED SUCCESSFULLY!" -ForegroundColor Green
    Write-Host "==============================================================================" -ForegroundColor Cyan
    Write-Host "`nDashboards & Web UIs Ready to View:" -ForegroundColor White
    Write-Host "  • Grafana Dashboard:   http://localhost:3000 (admin / admin)" -ForegroundColor Yellow
    Write-Host "  • Prometheus Alerts:   http://localhost:9090/alerts" -ForegroundColor Yellow
    Write-Host "  • Kafka Web UI:        http://localhost:8080" -ForegroundColor Yellow
    Write-Host "  • FastAPI Docs:        http://localhost:8001/docs" -ForegroundColor Yellow
    Write-Host "  • API Metrics:         http://localhost:8001/metrics" -ForegroundColor Yellow
} else {
    Write-Host "`n[!] Pipeline stopped due to an error. Check error logs above." -ForegroundColor Red
}
