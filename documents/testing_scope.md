# Testing Scope
## Behavioral Observability Framework for KYC Onboarding
Pranali Pandharinath Supekar (2024DA04387)

This document records the test pyramid scope decision for this project,
per the evaluator feedback: "implement test pyramid: unit, integration,
end-to-end, and regression with synthetic & frozen holdouts."

---

## Test Pyramid — What's Implemented, and How

| Tier | Status | Where | Notes |
|---|---|---|---|
| **Unit** | Automated in CI | `tests/test_drift_detection.py`, `tests/test_kafka_consumer_etl.py` | Pure-logic functions only — no live DB/Kafka connection. Targets the exact functions that broke silently during development (real-time feature normalization, PSI computation) — see `project_development_log.md`. |
| **Integration** | Manual, documented | `runbook.md` Section 2 (Normal Operation Sequence) | Requires the local Docker stack (Postgres + Kafka). Run the full sequence — ingest → feature-engineer → quality-check → produce → consume → drift-check — against real services. |
| **End-to-end** | Manual, demonstrated | Kafka producer → consumer → Grafana dashboard | Verified working in practice: producer publishes real BAF records, consumer scores them in real time, Prometheus/Grafana show live metrics. See dev log for the confirmed working run (200 events processed, 8 anomalies flagged, 0 errors). |
| **Regression (synthetic & frozen holdouts)** | Partially implemented | `drift_detection.py`'s synthetic-drift fallback | When `real_time_scores` isn't populated, the script generates a synthetic sample with deliberately injected drift (mimicking a coordinated bot-onboarding attack pattern) — this was used to verify the ALERT path fires correctly, not just the PASS path. A frozen numeric holdout set (e.g. a fixed sample of `behavioral_features` with known expected model output, checked on every run) is flagged as a natural extension, not yet implemented. |

---

## Why Integration/E2E Tests Are Not Automated in CI

Spinning up PostgreSQL, Kafka (KRaft mode), and loading a 1M-row dataset
inside a GitHub Actions runner is achievable but adds meaningful CI
runtime, complexity (service containers, wait-for-healthy logic, seed
data provisioning), and maintenance overhead relative to the time
available before the dissertation deadline. This is a **deliberate,
documented scope decision** for a PoC-level project, not an oversight:

- Unit tests catch the class of bug that actually occurred during
  development (silent logic errors in pure functions — e.g. the
  normalization-range bug that caused 100% of events to be flagged
  anomalous) — this is the highest-value, lowest-cost tier to automate.
- Integration/E2E correctness was manually verified multiple times
  during development (see `project_development_log.md`) and is
  reproducible on demand via the documented `runbook.md` sequence.
- A production deployment of this system would extend `ci.yml` with
  Postgres/Kafka service containers and a seeded test database — this
  is noted as future work, consistent with the "harden after MVI"
  guidance in the evaluator feedback.

## What CI Actually Runs (see `.github/workflows/ci.yml`)

1. Syntax/import check across all `.py` files (catches broken imports
   or syntax errors immediately — a lightweight stand-in for the
   "schema validation" CI/CD gate requested in feedback)
2. Unit test suite (`pytest tests/`)
3. A syntax-only check of `data_quality_checks.py` (full execution
   requires a live database, so this is intentionally scoped to
   catching code-level breakage, not data-level issues, in CI)

Full data quality validation (the actual 29-check suite against real
data) is run manually, documented in `runbook.md` Section 2.
