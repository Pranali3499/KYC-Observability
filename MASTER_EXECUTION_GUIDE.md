# Master Execution Guide — KYC Behavioral Observability Framework
## Complete End-to-End Pipeline Execution Order & Expected Outputs
**Student:** Pranali Pandharinath Supekar (2024DA04387) | M.Tech DSE, BITS Pilani WILP

This document provides the single, sequential master guide to execute the entire KYC Behavioral Observability Framework from start to finish.

---

## 🛠️ Prerequisites & Infrastructure

Before running the Python scripts, ensure Docker Desktop is running and start the infrastructure containers:

```bash
# Terminal 1: Navigate to repository & start containers
cd D:\kyc-observability
docker compose up -d

# Verify 6 containers are healthy (postgres, kafka, kafka-ui, prometheus, grafana, node-exporter)
docker ps
```

---

## 📋 End-to-End Execution Sequence

```
[Stage 0: Pre-Flight Verification]
  └── pytest tests/ -v                                     (~2 min) -> Asserts 55 tests pass

[Stage 1: Pre-Ingestion Data Validation & Deduplication]
  └── python pre_ingestion_validator.py                    (~2 sec) -> Schema + nulls + SHA-256 dedup

[Stage 2: Database Ingestion & Feature Engineering]
  ├── python data_ingestion.py --csv Base.csv             (~30 sec) -> Loads 1M rows into Postgres
  ├── python feature_engineering.py                       (~40 sec) -> Derives 6 behavioral risk scores
  └── python data_quality_checks.py                       (~5 sec)  -> Post-ingestion DB quality gate

[Stage 3: Cross-Dataset Model Generalization]
  └── python cross_dataset_evaluation.py --sample-size 50000 (~20 sec) -> Evaluates Base + Variant I to V

[Stage 4: Hyperparameter Tuning, Feature Ablation & Explainability]
  ├── python mlflow_optuna_tuning.py --n-trials 10        (~45 sec) -> Tunes Isolation Forest in MLflow
  ├── python feature_ablation.py                          (~30 sec) -> Leave-one-out feature ranking
  ├── python shap_explainability.py --n-samples 2000      (~40 sec) -> SHAP global & local attributions
  └── python counterfactual_analysis.py --n-samples 500   (~15 sec) -> Actionable recourse flips

[Stage 5: Biometric Sub-components & Parquet ETL]
  ├── python biometric_face_matching.py                   (~15 sec) -> LFW Face Pair AUC ~ 0.694
  ├── python biometric_liveness_detection.py              (~10 sec) -> Liveness AUC ~ 0.523 (Honest negative)
  ├── python document_ocr.py                              (~5 sec)  -> Synthetic OCR confidence
  ├── python identity_mismatch_detection.py               (~5 sec)  -> Identity mismatch detection
  ├── python biometric_etl_normalize.py                   (~5 sec)  -> Normalizes 4 tables to Parquet
  ├── python biometric_etl_combine.py                     (~5 sec)  -> Unifies into feature-ready Parquet
  └── python verify_biometric_go_no_go.py                 (~2 sec)  -> Automated [GO] decision gate

[Stage 6: Real-Time Streaming & Synchronous Scoring]
  ├── python kafka_producer.py --n-events 100 --delay 0.05       (~5 sec) -> Publishes tabular onboarding events
  ├── python kafka_consumer_etl.py --max-messages 100            (~5 sec) -> Scores & persists real_time_scores
  ├── python kafka_biometric_producer.py --n-events 50 --delay 0.05 (~3 sec) -> Publishes biometric events
  └── python kafka_biometric_consumer_etl.py --max-messages 50   (~3 sec) -> Scores & persists biometric scores

[Stage 7: Drift Detection, Retraining & Canary Rollout]
  ├── python drift_detection.py                           (~10 sec) -> Evaluates PSI & KS drift
  ├── python drift_metrics_exporter.py --once             (~2 sec)  -> Exports gauges to Prometheus (:8002)
  ├── python retraining_pipeline.py --simulate-drift      (~5 sec)  -> Fits candidate model on drift alert
  └── python canary_rollout_simulator.py                  (~5 sec)  -> 10% -> 50% -> 100% canary rollout

[Stage 8: Observability UI Verification]
  ├── Open Prometheus: http://localhost:9090/alerts       -> Inspect active alerting rules & targets
  └── Open Grafana:    http://localhost:3000              -> View real-time 10-panel dashboard
```

---

## 🔍 Stage-by-Stage Command Details & Expected Outputs

---

### Stage 0: Automated Test Pyramid Verification
- **Command:**
  ```bash
  venv\Scripts\pytest.exe tests/ -v
  ```
- **What it does:** Runs all 55 tests across unit, regression baselines, live database integration, and synthetic end-to-end pipeline tests.
- **Expected Output:**
  ```
  ======================= 55 passed in ~160s (100% PASS) =======================
  ```

---

### Stage 1: Pre-Ingestion Data Validation & Deduplication
- **Command:**
  ```bash
  venv\Scripts\python.exe pre_ingestion_validator.py --csv ci_test_data.csv
  ```
- **What it does:** Validates schema contracts, null rates (< 1%), value ranges, and performs SHA-256 hash deduplication *prior* to inserting data into PostgreSQL.
- **Expected Output:**
  ```
  --- Gate 1: Schema Contract Validation --- [PASS] All 10 critical columns present.
  --- Gate 2: Null Rate Gate ---             [PASS] All critical columns < 1.0% nulls.
  --- Gate 3: Value Range Validation ---     [OK] All fields in range.
  --- Gate 4: Record-Level SHA-256 Dedup --- [PASS] Processed & clean records surviving.
  PRE-INGESTION VALIDATION SUMMARY: [PASS - ALL GATES OK]
  ```

---

### Stage 2: Database Ingestion & Behavioral Feature Engineering
- **Commands:**
  ```bash
  venv\Scripts\python.exe data_ingestion.py --csv Base.csv
  venv\Scripts\python.exe feature_engineering.py
  venv\Scripts\python.exe data_quality_checks.py
  ```
- **What they do:**
  1. `data_ingestion.py`: Loads the raw BAF dataset into PostgreSQL table `kyc_transactions`.
  2. `feature_engineering.py`: Cleans `-1` sentinels and engineers the 6 behavioral risk scores into `behavioral_features`.
  3. `data_quality_checks.py`: Post-ingestion verification asserting schema and data integrity in the DB.
- **Expected Output:**
  ```
  Generated feature records: 1,000,000
  [demo2] PASS -- Behavioral features successfully generated.
  DATA QUALITY SUMMARY: [PASS - ALL CHECKS OK]
  ```

---

### Stage 3: Cross-Dataset Generalization (Base + Variant I to V)
- **Command:**
  ```bash
  venv\Scripts\python.exe cross_dataset_evaluation.py --sample-size 50000
  ```
- **What it does:** Evaluates the tuned Isolation Forest against Base and all 5 alternative fraud variant datasets (~250MB each), calculating ROC-AUC, detection rates, and PSI shifts.
- **Expected Output:**
  ```
  ======================================================================
  CROSS-DATASET EVALUATION SUMMARY
  ======================================================================
           Dataset  Rows Evaluated  Fraud Rate (%)  ROC-AUC  Detection Rate @ 5%
  Base (Reference)           50000           1.15%   0.5486                9.95%
         Variant I           50000           1.08%   0.5318                7.02%
        Variant II           50000           1.17%   0.5862               11.30%
       Variant III           50000           1.18%   0.5592                7.99%
        Variant IV           50000           1.19%   0.5956                9.75%
         Variant V           50000           1.18%   0.5479                8.31%
  Summary table saved to 'cross_dataset_summary.csv'
  ROC Curves plot saved to 'cross_dataset_roc_curves.png'
  ```

---

### Stage 4: Hyperparameter Tuning, Feature Ablation & Explainability
- **Commands:**
  ```bash
  venv\Scripts\python.exe mlflow_optuna_tuning.py --n-trials 10
  venv\Scripts\python.exe feature_ablation.py
  venv\Scripts\python.exe shap_explainability.py --n-samples 2000
  venv\Scripts\python.exe counterfactual_analysis.py --n-samples 500
  ```
- **What they do:**
  1. `mlflow_optuna_tuning.py`: Optuna search optimizing Isolation Forest AUC, logging trials to MLflow.
  2. `feature_ablation.py`: Leave-one-out importance ranking `device_reuse_score` as #1.
  3. `shap_explainability.py`: Computes SHAP values and saves `shap_summary_plot.png`.
  4. `counterfactual_analysis.py`: Computes actionable recourse and saves `counterfactual_summary_plot.png`.
- **Expected Output:**
  ```
  Tuned Model AUC: ~0.5964
  Top Risk Driver: device_reuse_score
  SHAP summary plot saved to 'shap_summary_plot.png'
  Counterfactual summary plot saved to 'counterfactual_summary_plot.png'
  ```

---

### Stage 5: Biometric Sub-Components & Parquet ETL
- **Commands:**
  ```bash
  venv\Scripts\python.exe biometric_face_matching.py
  venv\Scripts\python.exe biometric_liveness_detection.py
  venv\Scripts\python.exe document_ocr.py
  venv\Scripts\python.exe identity_mismatch_detection.py
  venv\Scripts\python.exe biometric_etl_normalize.py
  venv\Scripts\python.exe biometric_etl_combine.py
  venv\Scripts\python.exe verify_biometric_go_no_go.py
  ```
- **What they do:** Validates all 4 biometric sub-components, normalizes validation outputs to Parquet, merges them into `biometric_features_combined.parquet`, and runs the Go/No-Go gate.
- **Expected Output:**
  ```
  Face Match AUC: ~0.6940 (LFW pairs)
  Liveness AUC: ~0.5228 (Honest negative baseline)
  Document OCR Mean Confidence: 95.1%
  Identity Mismatch Detection: 78.6%
  Combined Parquet: 1,541 rows written -> biometric_parquet/biometric_features_combined.parquet
  BIOMETRIC VALIDATION GATE: [GO - VALIDATION READY FOR REPORTING]
  ```

---

### Stage 6: Real-Time Streaming (Kafka) & Synchronous Serving
- **Commands:**
  ```bash
  # Step 6.1: Tabular Onboarding Stream
  venv\Scripts\python.exe kafka_producer.py --n-events 100 --delay 0.05
  venv\Scripts\python.exe kafka_consumer_etl.py --max-messages 100

  # Step 6.2: Biometric Verification Stream
  venv\Scripts\python.exe kafka_biometric_producer.py --n-events 50 --delay 0.05
  venv\Scripts\python.exe kafka_biometric_consumer_etl.py --max-messages 50
  ```
- **What they do:** Streams onboarding and biometric events through Kafka topics (`kyc-onboarding-events`, `kyc-biometric-events`), validates schemas, computes scores in real time, persists to PostgreSQL feature tables (`real_time_scores`, `biometric_real_time_scores`), and exports Prometheus metrics.
- **Expected Output:**
  ```
  Processed 100 events | Anomalies flagged: ~1-5% | P95 latency: ~25-45ms (< 100ms target)
  Processed 50 biometric events | Spoofs flagged: ~8% | P95 latency: ~15-25ms
  ```

---

### Stage 7: Continuous Drift Detection, Retraining & Canary Rollouts
- **Commands:**
  ```bash
  venv\Scripts\python.exe drift_detection.py
  venv\Scripts\python.exe drift_metrics_exporter.py --once
  venv\Scripts\python.exe retraining_pipeline.py --simulate-drift
  venv\Scripts\python.exe canary_rollout_simulator.py
  ```
- **What they do:**
  1. `drift_detection.py`: Compares live scoring distribution against reference data using 10-bin PSI and KS tests.
  2. `drift_metrics_exporter.py`: Exports PSI/KS gauges to Prometheus on port `:8002`.
  3. `retraining_pipeline.py`: Automatically trains a candidate Isolation Forest when drift occurs (`PSI > 0.25`) and logs to MLflow.
  4. `canary_rollout_simulator.py`: Progresses traffic (10% -> 50% -> 100%), evaluates latency & error gates, and executes automated rollback if breached.
- **Expected Output:**
  ```
  [OK] Drift report written to drift_report table
  [ALERT] Retraining triggered on drift! Candidate model trained and evaluated.
  --- Running Stage 1 (10% Canary) --- [PASS] P95 latency: ~23ms, Error: 0.00%
  --- Running Stage 2 (50% Canary) --- [PASS] P95 latency: ~28ms, Error: 0.00%
  --- Running Stage 3 (100% Full Promotion) --- [PASS]
  CANARY ROLLOUT VERDICT: [SUCCESS - CANDIDATE PROMOTED TO CHAMPION]
  ```

---

### Stage 8: Observability UI Verification
- **Prometheus UI:** Open `http://localhost:9090/alerts` and `http://localhost:9090/targets`
  - Verify all 4 scrape targets (`kyc-consumer`, `kyc-api`, `drift-metrics`, `node-exporter`) are green (`UP`).
- **Grafana Dashboard:** Open `http://localhost:3000` (Login: `admin` / `admin`)
  - Navigate to **"KYC Behavioral Observability - Full Stack Monitoring"**
  - Verify panels for: Consumer Status, Events Processed, Anomaly Rate, Inference Latency Percentiles, PSI/KS Drift Gauges, Feature Store Write Latency, and Node CPU/Memory.

---

## 🎯 Summary Execution Command (Batch Run All)

If you wish to execute the complete pipeline sequentially in one command, you can run:

```bash
venv\Scripts\python.exe -c "import subprocess, sys; scripts = ['pre_ingestion_validator.py', 'cross_dataset_evaluation.py', 'verify_biometric_go_no_go.py', 'drift_detection.py', 'retraining_pipeline.py', 'canary_rollout_simulator.py']; [subprocess.run([sys.executable, s], check=True) for s in scripts]"
```
