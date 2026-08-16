# KYC Behavioral Observability Framework

![CI](https://github.com/Pranali3499/KYC-Observability/actions/workflows/ci.yml/badge.svg)
![Tests](https://github.com/Pranali3499/KYC-Observability/actions/workflows/tests.yml/badge.svg)

**A Behavioral Observability Framework for Early Risk Assessment in KYC Onboarding**

M.Tech Dissertation | Pranali Pandharinath Supekar | 2024DA04387 | BITS Pilani WILP, M.Tech Data Science and Engineering

---

## What this project does

Instead of only checking documents against static rules one application at a time, this framework watches **behavioral patterns** during KYC onboarding — device reuse, submission velocity, address stability — to flag anomalous applications early, before a fraud label ever exists. Every flag comes with an explanation (SHAP + counterfactual analysis), and the whole system is monitored end-to-end with real-time scoring, drift detection, and automated alerting.

## Architecture — 7 layers

```
Layer 1: Data           -->  PostgreSQL storage of raw BAF dataset + git-linked provenance
Layer 2: Feature Eng.    -->  6 behavioral risk scores derived from raw fields
Layer 3: Anomaly Det.    -->  Isolation Forest, tuned with Optuna, tracked in MLflow
Layer 4: Explainability  -->  SHAP (why flagged) + Counterfactual (what would un-flag it)
Layer 5: Biometric Auth  -->  4 independently validated sub-components
Layer 6: Production      -->  Kafka (async) + FastAPI (sync) real-time scoring
Layer 7: Observability   -->  Prometheus + Grafana + drift detection + analyst dashboard
```

## Key results

- **Detection:** AUC 0.5678 → 0.5964 after Optuna tuning (30 trials); true positives 267 → 854 (>3x)
- **Explainability:** SHAP and independent feature ablation both rank `device_reuse_score` as the top signal
- **Real-time latency:** P95 ≈ 30-50ms, well under the 100ms target
- **Drift detection:** validated in both directions (PASS on stable data, ALERT on induced drift), including a specific finding where PSI alone missed a shift that KS caught

Full results, methodology, and honest discussion of limitations are in the dissertation report.

## Getting started

See [`RUNBOOK.md`](RUNBOOK.md) for full setup instructions, the complete execution order, and known issues with fixes.

Quick start:
```bash
git clone https://github.com/Pranali3499/KYC-Observability.git
cd KYC-Observability
python -m venv venv
venv\Scripts\activate        # or source venv/bin/activate on macOS/Linux
pip install -r requirements.txt
docker compose up -d
python data_ingestion.py --csv Base.csv
python feature_engineering.py
python data_quality_checks.py
```

## Testing

This project has 4 layers of automated testing, all running in CI on every push:

- **Unit tests** (`tests/test_*.py`) — feature engineering math, drift PSI/KS logic, counterfactual analysis, real-time scoring, alert formatting
- **Regression tests** (`tests/test_regression_baseline.py`) — frozen feature list, drift thresholds, tuned model hyperparameter bounds
- **Integration tests** (`tests/test_integration_pipeline.py`) — real Kafka producer/consumer flow, drift detection, data quality checks against live local infrastructure (run manually, not in CI — see file docstring for why)
- **Data pipeline CI gate** — real ingestion → feature engineering → data quality checks, run automatically against an isolated, throwaway Postgres instance in CI

## Project structure

Key scripts, in typical execution order:

| Script | Purpose |
|---|---|
| `data_ingestion.py` | Loads the BAF dataset into PostgreSQL |
| `feature_engineering.py` | Derives 6 behavioral risk scores |
| `data_quality_checks.py` | Schema/null-rate/value-range validation gate |
| `mlflow_optuna_tuning.py` | Hyperparameter tuning (Isolation Forest) |
| `feature_ablation.py` | Leave-one-out feature importance |
| `shap_explainability.py` | Per-record and global SHAP explanations |
| `counterfactual_analysis.py` | "What would need to change" analysis |
| `biometric_face_matching.py` / `biometric_liveness_detection.py` / `document_ocr.py` / `identity_mismatch_detection.py` | Independent biometric sub-component validation |
| `biometric_etl_normalize.py` / `biometric_etl_combine.py` | Normalize and unify biometric results into feature-ready parquet |
| `kafka_producer.py` / `kafka_consumer_etl.py` | Real-time streaming scoring pipeline |
| `api.py` | Synchronous FastAPI scoring endpoint |
| `drift_detection.py` | PSI/KS drift monitoring |
| `analyst_dashboard.py` | Streamlit case-review interface |

## Known limitations

Stated explicitly rather than glossed over — see the dissertation report's Limitations section and [`dataset_model_change_registry.md`](dataset_model_change_registry.md) for full detail. Notably: liveness detection (AUC 0.523) is an honest negative result kept future work, not hidden; results are validated on the BAF dataset only, with no cross-dataset generalization claim.
