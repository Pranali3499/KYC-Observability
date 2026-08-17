# Runbook — Behavioral Observability Framework for KYC Onboarding
## Pranali Pandharinath Supekar (2024DA04387)

Operational reference for running, monitoring, evaluating, and recovering the KYC Behavioral Observability Framework.

---

## 1. Architecture & Services

The system runs across 7 core layers:
- **Layer 1 (Data):** PostgreSQL storage + git-linked audit provenance
- **Layer 2 (Feature Engineering):** 6 behavioral risk scores (`feature_engineering.py`)
- **Layer 3 (Anomaly Detection):** Tuned Isolation Forest (`isolation_forest_tuned.pkl`, MLflow)
- **Layer 4 (Explainability):** SHAP + Counterfactual recourse analysis
- **Layer 5 (Biometric Validation):** 4 independently validated sub-components & unified Parquet ETL
- **Layer 6 (Production Serving):** Real-time Kafka streaming (`kafka_consumer_etl.py`) + Synchronous FastAPI (`api.py`)
- **Layer 7 (Observability):** Prometheus metrics, Grafana dashboards, Alert rules, PSI/KS drift detection

---

## 2. Infrastructure Setup & Endpoints

### 2.1 Starting Local Stack
```bash
cd D:\kyc-observability
docker compose up -d
docker ps          # confirms postgres, kafka, kafka-ui, prometheus, grafana, node-exporter
source venv/Scripts/activate  # on Windows: venv\Scripts\activate
```

### 2.2 Active Service Endpoints
| Service | URL / Port | Purpose |
|---|---|---|
| **FastAPI Scoring API** | `http://localhost:8001/docs` | Synchronous risk scoring |
| **API Prometheus Metrics** | `http://localhost:8001/metrics` | FastAPI request & latency metrics |
| **Streaming Consumer Metrics** | `http://localhost:8000/metrics` | Kafka consumer throughput & inference latency |
| **Drift Metrics Exporter** | `http://localhost:8002/metrics` | PSI/KS drift metric gauges |
| **Biometric Consumer Metrics** | `http://localhost:8003/metrics` | Real-time biometric stream metrics |
| **Node Exporter** | `http://localhost:9100/metrics` | Host/Container CPU, RAM, Disk metrics |
| **Prometheus UI & Alerts** | `http://localhost:9090` | Alert evaluation & targets overview |
| **Grafana Dashboard** | `http://localhost:3000` | Real-time monitoring (`admin`/`admin`) |
| **Kafka Web UI** | `http://localhost:8080` | Topic browser & message inspection |

---

## 3. End-to-End Execution Sequence

### Step 1: Pre-Ingestion Validation & Ingestion
```bash
# Validate incoming data schema & deduplicate records
python pre_ingestion_validator.py --csv Base.csv --output-clean clean_base.parquet

# Ingest into PostgreSQL and engineer features
python data_ingestion.py --csv Base.csv
python feature_engineering.py

# Run post-ingestion data quality gate
python data_quality_checks.py
```

### Step 2: Cross-Dataset Generalization Validation
```bash
# Evaluate model generalization across Base and Variant I through Variant V
python cross_dataset_evaluation.py --sample-size 50000
```

### Step 3: Biometric Sub-components & Parquet ETL
```bash
# Run biometric validations
python biometric_face_matching.py
python biometric_liveness_detection.py
python document_ocr.py
python identity_mismatch_detection.py

# Normalize and combine into feature-ready parquet
python biometric_etl_normalize.py
python biometric_etl_combine.py

# Automated Biometric Go/No-Go gate check
python verify_biometric_go_no_go.py
```

### Step 4: Real-Time Streaming & Synchronous Serving
```bash
# Start synchronous API
uvicorn api:app --port 8001 --reload

# Produce & Consume Onboarding Events (Kafka)
python kafka_producer.py --n-events 200 --delay 0.1
python kafka_consumer_etl.py --max-messages 200

# Produce & Consume Biometric Events (Kafka)
python kafka_biometric_producer.py --n-events 50 --delay 0.2
python kafka_biometric_consumer_etl.py --max-messages 50
```

### Step 5: Drift Detection & Automated Retraining
```bash
# Run PSI / KS Drift Detection
python drift_detection.py

# Start Drift Prometheus Exporter
python drift_metrics_exporter.py --once

# If drift detected, trigger automated retraining & canary rollout
python retraining_pipeline.py --simulate-drift
python canary_rollout_simulator.py
```

---

## 4. Troubleshooting & Incident Response

| Symptom | Likely Cause | Detection | Remediation |
|---|---|---|---|
| **Prometheus target DOWN** | Service not started | `http://localhost:9090/targets` | Start corresponding service (`api.py`, `kafka_consumer_etl.py`, `drift_metrics_exporter.py`). |
| **HighInferenceLatency Alert** | P95 latency > 100ms | Prometheus alert `HighInferenceLatencyP95` | Check DB indexing, inspect CPU utilization in Node Exporter, or scale consumers. |
| **ModelOutputDrift Alert** | Output distribution shifted (PSI > 0.25) | Prometheus alert `ModelOutputDrift` | Run `retraining_pipeline.py`, inspect SHAP feature shifts (`shap_explainability.py`). |
| **Kafka Connection Timeout** | Broker starting or listener issue | `KafkaTimeoutError` | Check `docker logs kyc-kafka` and confirm port 9092 is exposed. |
| **Biometric NO-GO Gate Failure** | Missing parquet/model artifacts | `verify_biometric_go_no_go.py` exits 1 | Run `biometric_face_matching.py` and `biometric_etl_combine.py` to regenerate artifacts. |

---

## 5. Rollback & Recovery Procedures

### Model Rollback:
```bash
git log --oneline -- isolation_forest_tuned.pkl
git checkout <commit_hash> -- isolation_forest_tuned.pkl
```

### Kafka Offset Reset:
```bash
docker exec -it kyc-kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --group kyc-etl-consumer-group \
  --reset-offsets --to-earliest \
  --topic kyc-onboarding-events --execute
```

---

## 6. Project Contacts

| Role | Name | Organization |
|---|---|---|
| **Student / Author** | Pranali Pandharinath Supekar (2024DA04387) | BITS Pilani WILP, M.Tech DSE |
| **Faculty Mentor** | Prof. A. Abdul Rahman | BITS Pilani WILP |
| **Industry Supervisor** | Srinivas Rao Marripelli | Technical Lead, TCS |
