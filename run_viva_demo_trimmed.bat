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
echo  DATA VOLUME USED IN THIS DEMO (vs. the full dissertation)
echo =============================================================
echo   Data quality checks   : FULL dataset  (1,000,000 rows -- not sampled)
echo   Model tuning / ablation: shown, not rerun (already validated on 1,000,000 rows)
echo   SHAP explainability    : shown, not rerun (full report run used 26,143 records)
echo   Counterfactual analysis: 300 records   (report's cited figure used 2,000)
echo   Face matching           : FULL LFW test set (1,000 pairs -- fixed, not sampled)
echo   Liveness detection      : FULL corpus (1,081 real / 960 fake -- fixed, not sampled)
echo   Document OCR            : 10 documents (report's cited figure used 30)
echo   Identity mismatch       : 20 cases     (report's cited figure used 50)
echo   Kafka producer/consumer : 30 events    (report's cited figure used 200)
echo   Drift detection         : live real_time_scores table (accumulates across runs)
echo   Unit tests               : all 37 tests, full suite -- not reduced
echo =============================================================
echo  Sample sizes below full-report figures are a DELIBERATE
echo  time-budget choice for a live demo, not a limitation of
echo  the underlying code -- every script accepts the full size
echo  as a command-line argument, and the reported dissertation
echo  figures were generated at those full sizes.
echo =============================================================
pause

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
echo  STEP 4/10 -- SHAP Explainability  (already computed -- NOT rerun live)
echo  A live SHAP run here would take a fixed ~6-7 min minimum
echo  (deep trees from this tuned model's max_samples~0.42 --
echo  the TreeExplainer build cost alone is ~340 sec, before any
echo  actual explaining happens). That is dead air in front of
echo  an evaluator, so this step opens the already-generated
echo  summary plot instead. Say out loud: "the full run on all
echo  26,143 flagged records takes about 3 hours end to end --
echo  here's the completed output from that run."
echo =============================================================
start "" shap_summary_plot.png
echo   Opened shap_summary_plot.png
echo   Top feature confirmed: device_reuse_score (matches ablation, Step 3)
pause

echo.
echo =============================================================
echo  STEP 5/10 -- Counterfactual Analysis  (LIVE -- ~30-60 sec)
echo  Expect median shift close to 22%% -- this is the corrected
echo  finding discussed in Chapter 7 of the report.
echo =============================================================
python counterfactual_analysis.py --n-samples 300
REM   (report's official figure: --n-samples 2000, median shift 22%%)

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
