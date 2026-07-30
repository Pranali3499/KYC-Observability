# Runbook — Behavioral Observability Framework for KYC Onboarding
## Pranali Pandharinath Supekar (2024DA04387)

Operational reference for running, monitoring, and recovering the
pipeline. Written for PoC/dissertation-demo scope — a production
deployment would extend this with on-call rotations and paging, not
just documented steps.

---

## 1. Starting the System

```bash
cd D:\kyc-observability
docker compose up -d
docker ps          # confirm 5 containers: postgres, kafka, kafka-ui, prometheus, grafana
source venv/Scripts/activate
```

Endpoints once running:
| Service | URL |
|---|---|
| Kafka UI | http://localhost:8080 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin/admin) |
| Consumer metrics (only while consumer is running) | http://localhost:8000/metrics |

---

## 2. Normal Operation Sequence

```bash
# 1. Ingest / refresh data (only needed after a dataset change)
python data_ingestion.py --csv Base.csv
python feature_engineering.py

# 2. Verify data quality before trusting downstream results
python data_quality_checks.py

# 3. Simulate/produce onboarding events
python kafka_producer.py --n-events 200 --delay 0.2

# 4. Consume + score in real time (leave running for continuous monitoring)
python kafka_consumer_etl.py

# 5. Check for drift periodically
python drift_detection.py
```

---

## 3. What Can Break, and How to Detect It

| Symptom | Likely Cause | Detection | First Response |
|---|---|---|---|
| `PendingRollbackError` / `OperationalError` during ingestion | Oversized batch write exceeding PostgreSQL's parameter limit, or a stale connection | Script traceback mentions psycopg2/sqlalchemy | Reduce `CHUNK_SIZE` in `data_ingestion.py`; retry. See dev log for the exact fix already applied. |
| `password authentication failed` | Wrong DB credentials in `--db-url` or `db_config.py` | Immediate connection error | Confirm container env vars in `docker-compose.yml` match what scripts use (`kyc_user`/`kyc_pass`) |
| `KafkaTimeoutError: Unable to bootstrap` | Kafka container not fully started, or wrong client library | Producer/consumer hangs then times out | `docker logs kyc-kafka --tail 30` — confirm broker fully started; if using `kafka-python` instead of `confluent-kafka`, switch (protocol incompatibility, see dev log) |
| Consumer scores look implausible (e.g. 100% flagged anomalous) | Real-time normalization ranges mis-calibrated | Compare against `data_quality_report`/known ~1% fraud rate | Re-run `compute_feature_ranges()` — confirm it queries live `kyc_transactions`, not hardcoded bounds (this exact bug occurred during development, see dev log) |
| Prometheus target shows `DOWN` | Consumer not running, or Windows Firewall blocking port 8000 | Prometheus UI → Status → Targets | Confirm consumer is actively running; check Windows Firewall inbound rules for port 8000/9092 if persistent |
| Grafana panels show "No data" | Consumer not running (expected when idle) | Grafana dashboard "Consumer Status" panel shows DOWN | Run `kafka_consumer_etl.py`; this is often correct/expected behavior, not a bug |
| `drift_detection.py` reports ALERT | Genuine data drift, OR comparing against too small a live sample | Check `sample_size_warning` column in `drift_report` table | If live sample < 300 rows, treat as low-confidence; re-run with more accumulated live data before acting |
| Docker containers fail to pull images (`no such host`) | Docker Desktop networking stuck | `docker compose up -d` fails with DNS errors | Restart Docker Desktop fully (Quit → reopen); this resolved the issue during development |

---

## 4. Rollback / Recovery

**Model rollback:** every tuned model is git-committed (`isolation_forest_tuned.pkl`, `face_match_model.pkl`, `liveness_model.pkl`). To roll back to a previous version:
```bash
git log --oneline -- isolation_forest_tuned.pkl
git checkout <commit-hash> -- isolation_forest_tuned.pkl
```

**Data rollback:** re-run `data_ingestion.py` and `feature_engineering.py` from the original `Base.csv` — both are idempotent (`if_exists="replace"`).

**Consumer offset reset** (re-process already-consumed Kafka messages, e.g. after fixing a bug):
```bash
docker exec -it kyc-kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group kyc-etl-consumer-group --reset-offsets --to-earliest --topic kyc-onboarding-events --execute
```

**Full environment reset** (nuclear option — loses all data):
```bash
docker compose down -v
docker compose up -d
python data_ingestion.py --csv Base.csv
python feature_engineering.py
```

---

## 5. Retraining / Canary Trigger Policy

Per `drift_detection.py`'s thresholds:
- **PSI < 0.10:** stable, no action
- **PSI 0.10–0.25:** log and monitor, no immediate action
- **PSI > 0.25:** investigate root cause; consider re-running `mlflow_optuna_tuning.py` to retrain, and treat the new model as a canary (compare its metrics against the current production model in MLflow before promoting)

This is a documented decision rule for this PoC; it is not automated
(no auto-retraining trigger is wired up) — a production system would
schedule `drift_detection.py` as a periodic job and alert on ALERT
status via Grafana alert rules.

---

## 6. Contacts

| Role | Name |
|---|---|
| Student / Developer | Pranali Pandharinath Supekar (2024DA04387) |
| Faculty Mentor | A. Abdul Rahman, BITS Pilani WILP |
| Supervisor | Srinivas Rao Marripelli, Technical Lead, TCS |

---

## 7. Testing Scope (see also `.github/workflows/ci.yml`)

Unit tests (`tests/`) cover pure-logic functions that don't require a
live database or Kafka connection — the feature-normalization logic
and PSI/drift-classification logic specifically, since these are the
functions that broke silently during development (see dev log). CI
runs these automatically on every push, plus a syntax check across
all scripts.

Integration/end-to-end testing (full pipeline against live Postgres +
Kafka) is run **manually** using the sequence in Section 2, not
automated in CI — spinning up the full Docker stack inside a CI
runner was judged out of scope for this dissertation's timeline. This
is a documented, deliberate scope decision, not an oversight.
