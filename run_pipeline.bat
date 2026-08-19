@echo off
REM ==============================================================================
REM KYC Behavioral Observability Framework -- Universal Single-Trigger Launcher
REM Student: Pranali Pandharinath Supekar (2024DA04387) | M.Tech DSE, BITS Pilani
REM ==============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==============================================================================
echo   KYC BEHAVIORAL OBSERVABILITY FRAMEWORK -- SINGLE-TRIGGER MASTER RUNNER
echo   Student: Pranali Pandharinath Supekar (2024DA04387)
echo ==============================================================================

set MODE=full
if "%1" NEQ "" set MODE=%1

echo [*] Target Execution Mode: %MODE%
echo.

REM 1. Detect Python Virtual Environment
set PYTHON_CMD=python
if exist venv\Scripts\python.exe (
    set PYTHON_CMD=venv\Scripts\python.exe
    echo [*] Using local virtual environment: venv\Scripts\python.exe
) else (
    echo [*] Using system Python: %PYTHON_CMD%
)

REM 2. Start / Verify Docker Infrastructure Stack
echo.
echo [*] Step 0: Initializing Docker Stack (Postgres, Kafka, Prometheus, Grafana, Node-Exporter)...
docker compose up -d
if %ERRORLEVEL% NEQ 0 (
    echo [!] WARNING: Docker compose failed or Docker is not running.
    echo     Continuing with local Python pipeline execution...
) else (
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
)

REM 3. Execute Master Pipeline Orchestrator
echo.
echo [*] Launching Single-Trigger Master Pipeline Orchestrator (Mode: %MODE%)...
%PYTHON_CMD% run_all_pipeline.py --mode %MODE%

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ==============================================================================
    echo   [-] PIPELINE EXECUTION FAILED! Review error logs above.
    echo ==============================================================================
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ==============================================================================
echo   [+] SUCCESS: ALL PIPELINE STAGES COMPLETED VIA SINGLE TRIGGER!
echo ==============================================================================
echo.
echo   Interactive Telemetry & Monitoring Services:
echo   - Grafana Dashboard:          http://localhost:3000 (admin / admin)
echo   - Prometheus Active Alerts:   http://localhost:9090/alerts
echo   - Kafka Topic Management:     http://localhost:8080
echo   - FastAPI Scoring Docs:       http://localhost:8001/docs
echo   - Streaming Metrics:          http://localhost:8000/metrics
echo.
pause
