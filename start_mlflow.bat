@echo off
title MLflow Tracking Server
echo ===================================================
echo  Starting MLflow UI Server on http://127.0.0.1:5000
echo ===================================================
cd /d "D:\kyc-observability"
call "D:\kyc-observability\venv\Scripts\activate.bat"
python -m mlflow ui --host 127.0.0.1 --port 5000
pause
