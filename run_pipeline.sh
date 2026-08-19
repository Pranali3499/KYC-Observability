#!/usr/bin/env bash
# ==============================================================================
# KYC Behavioral Observability Framework -- Universal Single-Trigger Launcher
# Student: Pranali Pandharinath Supekar (2024DA04387) | M.Tech DSE, BITS Pilani
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-full}"

echo "=============================================================================="
echo "  KYC BEHAVIORAL OBSERVABILITY FRAMEWORK -- SINGLE-TRIGGER MASTER RUNNER"
echo "  Student: Pranali Pandharinath Supekar (2024DA04387)"
echo "=============================================================================="
echo "[*] Target Execution Mode: ${MODE}"
echo ""

# 1. Detect Python Environment
PYTHON_CMD="python3"
if [ -f "venv/bin/python" ]; then
    PYTHON_CMD="venv/bin/python"
    echo "[*] Using virtual environment: venv/bin/python"
elif [ -f "venv/Scripts/python.exe" ]; then
    PYTHON_CMD="venv/Scripts/python.exe"
    echo "[*] Using virtual environment: venv/Scripts/python.exe"
else
    echo "[*] Using default Python: $(which python3 || which python)"
fi

# 2. Check Docker Infrastructure
echo ""
echo "[*] Step 0: Checking Docker Container Stack..."
if command -v docker >/dev/null 2>&1; then
    docker compose up -d || docker-compose up -d || echo "[!] Docker start skipped."
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || true
else
    echo "[!] Docker not detected in PATH. Skipping container orchestration."
fi

# 3. Execute Master Pipeline Orchestrator
echo ""
echo "[*] Launching Single-Trigger Master Pipeline Orchestrator (Mode: ${MODE})..."
$PYTHON_CMD run_all_pipeline.py --mode "${MODE}"

echo ""
echo "=============================================================================="
echo "  [+] SUCCESS: ALL PIPELINE STAGES COMPLETED VIA SINGLE TRIGGER!"
echo "=============================================================================="
echo "  Interactive Telemetry & Monitoring Services:"
echo "  - Grafana Dashboard:          http://localhost:3000 (admin / admin)"
echo "  - Prometheus Active Alerts:   http://localhost:9090/alerts"
echo "  - Kafka Topic Management:     http://localhost:8080"
echo "  - FastAPI Scoring Docs:       http://localhost:8001/docs"
echo "  - Streaming Metrics:          http://localhost:8000/metrics"
echo "=============================================================================="
