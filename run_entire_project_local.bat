@echo off
REM ==============================================================================
REM KYC Behavioral Observability Framework -- Full Pipeline Master Batch Launcher
REM Student: Pranali Pandharinath Supekar (2024DA04387)
REM ==============================================================================

echo ==============================================================================
echo   KYC BEHAVIORAL OBSERVABILITY FRAMEWORK -- FULL PIPELINE LOCAL EXECUTION
echo ==============================================================================

cd /d "%~dp0"

echo.
echo [*] Step 0: Verifying Docker Stack (Postgres, Kafka, Prometheus, Grafana, Node-Exporter)...
docker compose up -d
docker ps

echo.
echo [*] Launching Master Pipeline Runner...
venv\Scripts\python.exe run_all_pipeline.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] An error occurred during pipeline execution. Check logs above.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ==============================================================================
echo   [+] ALL STAGES EXECUTED SUCCESSFULLY!
echo ==============================================================================
echo.
echo   You can now view your dashboards:
echo   - Grafana Dashboard:   http://localhost:3000 (admin / admin)
echo   - Prometheus Alerts:   http://localhost:9090/alerts
echo   - Kafka Web UI:        http://localhost:8080
echo   - FastAPI Docs:        http://localhost:8001/docs
echo   - API Metrics:         http://localhost:8001/metrics
echo.
pause
