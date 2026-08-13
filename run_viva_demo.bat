@echo off
REM =============================================================
REM  KYC BEHAVIORAL OBSERVABILITY FRAMEWORK -- VIVA DEMO SCRIPT
REM  Pranali Pandharinath Supekar | 2024DA04387
REM
REM  Target runtime: ~13-14 minutes end-to-end.
REM  Run this from D:\kyc-observability with venv already active
REM  and Docker containers already up (docker compose up -d).
REM
REM  DESIGN NOTE (read before you run this in front of an
REM  evaluator): full Optuna tuning (30 trials x 1M rows) and
REM  full feature ablation (6 retrains x 1M rows) are NOT rerun
REM  live here -- they would blow the time budget and, in the
REM  case of tuning, would OVERWRITE your already-validated
REM  isolation_forest_tuned.pkl with a possibly different result
REM  mid-demo. Those results are already validated and reported;
REM  this script displays them instead of recomputing them.
REM  Everything else below IS a real, live run.
REM =============================================================

echo.
echo =============================================================
echo  STEP 0/10 -- Environment check
echo =============================================================
docker ps
echo.
echo If you do NOT see 5-6 containers listed above as "Up",
echo stop now and run: docker compose up -d
echo.
pause

echo.
echo =============================================================
echo  STEP 1/10 -- Data Quality Validation  (~15-30 sec)
echo  Confirms the 1M-row dataset and engineered features are
echo  intact before anything else runs.
echo =============================================================
python data_quality_checks.py

echo.
echo =============================================================
echo  STEP 2/10 -- Model Tuning Result  (already computed -- NOT rerun)
echo  Full 30-trial Optuna search + MLflow logging already done.
echo  Reusing the validated artifact: isolation_forest_tuned.pkl
echo =============================================================
echo   Baseline AUC  : 0.5678   (n_estimators=100, mid-sem)
echo   Tuned AUC     : 0.5964   (30-trial Optuna search)
echo   True positives: 267 -^> 854   (more than 3x)
echo   Flagged       : 11,000 -^> 26,143
echo   [Full trial history is browsable at http://localhost:5000
echo    if you started 'mlflow ui' -- optional, not required here]
pause

echo.
echo =============================================================
echo  STEP 3/10 -- Feature Importance  (already computed -- NOT rerun)
echo  Full leave-one-out ablation (6 retrains on 1M rows) already
echo  done. Showing the validated ranking, cross-checked against
echo  SHAP in Step 4.
echo =============================================================
echo   Rank 1: device_reuse_score          (AUC drop +0.0385)
echo   Rank 2: address_stability_score     (AUC drop +0.0354)
echo   Rank 3: financial_risk_score        (AUC drop +0.0353)
echo   Rank 4: geographic_risk_score       (AUC drop +0.0185)
echo   Rank 5: session_velocity_score      (AUC drop +0.0072)
echo   Rank 6: identity_consistency_score  (AUC drop +0.0003)
pause

echo.
echo =============================================================
echo  STEP 4/10 -- SHAP Explainability  (LIVE -- ~6-7 min)
echo  This is the slowest live step. Building the TreeExplainer
echo  for this tuned model takes a fixed ~340 sec regardless of
echo  sample size (deep trees from max_samples~0.42). Kept to a
echo  small sample here specifically to stay inside the time
echo  budget -- explain this fixed cost to your evaluator while
echo  it runs, it is a genuine finding from this project, not
echo  a stall.
REM  If you are tight on time, Ctrl+C this step and instead
REM  open shap_summary_plot.png (already generated earlier).
echo =============================================================
python shap_explainability.py --n-samples 200

echo.
echo =============================================================
echo  STEP 5/10 -- Counterfactual Analysis  (LIVE -- ~30-60 sec)
echo  Expect median shift close to 22%% -- this is the corrected
echo  finding discussed in Chapter 7 of the report.
echo =============================================================
python counterfactual_analysis.py --n-samples 300

echo.
echo =============================================================
echo  STEP 6/10 -- Biometric Validation, all 4 components  (LIVE -- ~1-2 min)
echo =============================================================
echo   -- Face matching --
python biometric_face_matching.py
echo.
echo   -- Liveness detection (expect a HONEST NEGATIVE result, AUC ~0.52) --
python biometric_liveness_detection.py --data-dir liveness_data
echo.
echo   -- Document OCR --
python document_ocr.py --n-samples 10 --tesseract-path "C:\Program Files\Tesseract-OCR\tesseract.exe"
echo.
echo   -- Identity mismatch detection --
python identity_mismatch_detection.py --n-samples 20

echo.
echo =============================================================
echo  STEP 7/10 -- Real-Time Pipeline: Kafka Producer + Consumer  (LIVE -- ~15-30 sec)
echo =============================================================
python kafka_producer.py --n-events 30 --delay 0.05
python kafka_consumer_etl.py --max-messages 30

echo.
echo =============================================================
echo  STEP 8/10 -- Drift Detection: PASS path  (LIVE -- ~10 sec)
echo  Real, unmodified live data -- expect all features OK.
echo =============================================================
python drift_detection.py

echo.
echo =============================================================
echo  STEP 9/10 -- Drift Detection: ALERT path  (LIVE -- ~15 sec)
echo  Forces the synthetic-drift fallback to show the detector
echo  responds correctly in BOTH directions, not just the good one.
echo =============================================================
docker exec -it kyc-postgres psql -U kyc_user -d kyc_db -c "ALTER TABLE real_time_scores RENAME TO real_time_scores_backup;"
python drift_detection.py
docker exec -it kyc-postgres psql -U kyc_user -d kyc_db -c "ALTER TABLE real_time_scores_backup RENAME TO real_time_scores;"

echo.
echo =============================================================
echo  STEP 10/10 -- Full Unit Test Suite  (LIVE -- ~5 sec)
echo  37 tests covering feature engineering, drift math,
echo  counterfactual logic, real-time scoring, and alerting.
echo =============================================================
pytest tests/ -v

echo.
echo =============================================================
echo  DEMO COMPLETE.
echo  Optional, if time remains: open these in a browser --
echo    Grafana dashboard : http://localhost:3000  (admin login)
echo    Kafka UI          : http://localhost:8080
echo    FastAPI docs      : run "uvicorn api:app --port 8001" then
echo                        visit http://localhost:8001/docs
echo    Analyst dashboard : run "streamlit run analyst_dashboard.py"
echo =============================================================
pause
