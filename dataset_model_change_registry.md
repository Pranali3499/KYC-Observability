# Dataset & Model Change Registry
### KYC Behavioral Observability Framework | Pranali Pandharinath Supekar | 2024DA04387

> Addresses mid-sem evaluator feedback: *"Maintain registry for dataset/model updates."* This document is not a separate, manually-maintained log — it is a formatted view of the `data_provenance` table your project's `log_provenance()` helper already writes to automatically on every significant script run. The registry already existed functionally; this document makes it browsable and citable as a standalone artifact. Built from the **complete** history (91 rows, `id` 1–123), not just a recent slice.

---

## How this registry is generated (and how to regenerate it)

Every major script — data ingestion, feature engineering, model tuning, ablation, SHAP, counterfactual analysis, all 4 biometric validations, biometric ETL, Kafka consumer runs, drift detection — calls `log_provenance()` on completion, writing one row to `data_provenance` with:

| Column | What it records |
|---|---|
| `id` | Auto-incrementing row ID |
| `run_timestamp` | When the script finished |
| `script_name` | Which script produced this entry |
| `source_dataset` | What data was read |
| `target_table` | What was written (table, model file, or parquet output) |
| `row_count` | Records processed/produced |
| `pipeline_version` | The git commit hash active at the time of the run |
| `git_dirty` | `t` = uncommitted local changes were present when this ran (pipeline_version may not fully describe the exact code state); `f` = working tree was clean |
| `notes` | Free-text result summary (AUC, detection rates, etc.) |

**To regenerate this registry at any time:**
```bash
docker exec kyc-postgres psql -U kyc_user -d kyc_db -c "SELECT * FROM data_provenance ORDER BY run_timestamp ASC;"
```
(Use `docker exec` without `-it` when capturing output to a file or pasting elsewhere — `-it` invokes Postgres's interactive pager, which truncates long results instead of printing everything at once.)

---

## Known limitation #1: most runs are logged as `git_dirty = t`

Of the 91 logged runs, only **6** (`id` 1, 3, 4, 67, 68, 70) show `git_dirty = f` (clean working tree). The overwhelming majority of this project's actual runs happened with local uncommitted changes present. This means: for most entries, `pipeline_version` reflects the most recent **committed** state at that time, not necessarily the exact code that produced that specific row. This is an honest, acknowledged gap in the audit trail — not something to claim doesn't exist. If asked in viva: *"The provenance system correctly flags this itself via git_dirty — most runs during active development had uncommitted edits, which is expected for iterative work, but it does mean pipeline_version is an approximation, not a guarantee, for those specific runs. Fully clean-tree provenance is achievable by committing before every run, which is more realistic as a production discipline than during active development."*

## Known limitation #2: a gap exists in the ID sequence

`id` values jump from **4 directly to 37** — rows 5 through 36 (32 entries) do not exist in the table. This wasn't investigated further as part of this registry effort; possible explanations include a table reset, a period where `log_provenance()` calls failed silently, or entries manually deleted at some point — but this is genuinely unknown, not something to guess at with false confidence. Flagging this explicitly is more defensible than presenting the table as if the record were complete.

---

## Registry: full project history, major milestones (curated from all 91 real rows)

| Date | Script | Target | Rows | Version | Dirty | Result |
|---|---|---|---|---|---|---|
| 2026-07-25 | `feature_engineering.py` | behavioral_features | 1,000,000 | `0134e95` | f | First feature engineering run |
| 2026-07-25 | `data_ingestion.py` | kyc_transactions | 1,000,000 | `fdf928f` | f | Clean re-ingestion |
| 2026-07-26 | `mlflow_optuna_tuning.py` | isolation_forest_tuned.pkl | 1,000,000 | `ae43b76` | t | **First tuning attempt** — AUC=0.5902, 10 trials |
| 2026-07-26 | `mlflow_optuna_tuning.py` | isolation_forest_tuned.pkl | 1,000,000 | `ae43b76` | t | **Full 30-trial tuning** — AUC=0.5964 (report's official figure, confirmed reproducible from this early date) |
| 2026-07-27 | `feature_ablation.py` | ablation_results | 1,000,000 | `918d52e` | t | 6-feature leave-one-out ablation |
| 2026-07-27 | `biometric_face_matching.py` | face_match_model.pkl | 3,200 | `848605a` | t | AUC=0.6940 — first biometric validation run |
| 2026-07-27 | `biometric_liveness_detection.py` | liveness_model.pkl | 2,041 | `7e7bf4f` | t | AUC=0.5228 — honest negative, confirmed from the very first run, not a late discovery |
| 2026-07-31 | `identity_mismatch_detection.py` | identity_mismatch_results | 50 | `6b88ba3` | t | detection_rate=78.6% (report's official figure size, n=50) |
| 2026-07-31 | `document_ocr.py` | document_ocr_results | 30 | `f3e0e2a` | t | mean_confidence=95.1% (report's official figure size, n=30) |
| 2026-07-31 | `shap_explainability.py` | shap_explanations | **26,143** | `b440be9` | t | **Full-population SHAP run** — this is the actual source of the report's official Table 6.4 figures |
| 2026-07-31 | `counterfactual_analysis.py` | counterfactual_explanations | 2,000 | `b440be9` | t | Report's official 2,000-sample counterfactual run — 22% median shift finding |
| 2026-08-10 | `data_ingestion.py` | kyc_transactions | 1,000,000 | `efc6178` | f | **Fresh clean-tree re-ingestion** — start of this session's live verification work |
| 2026-08-10 | `feature_engineering.py` | behavioral_features | 1,000,000 | `efc6178` | f | Clean re-run |
| 2026-08-10 | `mlflow_optuna_tuning.py` | isolation_forest_tuned.pkl | 1,000,000 | `efc6178` | t | Live-verified reproduction, AUC=0.5964 confirmed again |
| 2026-08-10 | `feature_ablation.py` | ablation_results | 1,000,000 | `a2b5ce3` | f | Live-verified ablation reproduction |
| 2026-08-13 | `biometric_etl_normalize.py` | biometric_parquet | 30 | `a2b5ce3` | t | **2 of 4 biometric tables found** — face_match_results/liveness_results not yet populated |
| 2026-08-13 | `biometric_face_matching.py` | face_match_model.pkl, **face_match_results** | 3,200 | `a2b5ce3` | t | First run after adding per-record persistence |
| 2026-08-13 | `biometric_liveness_detection.py` | liveness_model.pkl, **liveness_results** | 2,041 | `a2b5ce3` | t | First run after adding per-record persistence |
| 2026-08-13 | `biometric_etl_normalize.py` | biometric_parquet | 1,541 | `a2b5ce3` | t | **All 4 of 4 biometric tables found** — gap closed, 0 missing |
| 2026-08-13 | `biometric_etl_combine.py` | biometric_features_combined.parquet | 1,541 | `7d62cf3` | t | Unified into one common-schema feature-ready table |
| 2026-08-14 | `kafka_consumer_etl.py` | real_time_scores | 200 | `0676ab5` | t | Post-alert-rules-fix verification run |
| 2026-08-14 | `drift_detection.py` | drift_report | 7 | `cdfd90f` | t | Most recent drift check on record |

*(This is a curated subset highlighting the significant milestones across the full 91-row history — every model tuning run, every biometric validation, every report-official-figure-size run, and the biometric-ETL gap-closure sequence. The complete raw table, including every Kafka consumer batch and every drift check, is retrievable via the query above at any time — it was not omitted from this document due to being unavailable, only for readability.)*

---

## What this registry makes visible — and why it matters

**1. Your report's official figures are traceable to specific, real runs, not just claims.** The `id=65` SHAP run (26,143 rows, commit `b440be9`, 2026-07-31) is the actual source of your report's Table 6.4. The `id=66` counterfactual run (2,000 rows, same commit) is the actual source of the 22% median-shift finding. These aren't retrospective claims — they were logged automatically, at the time, before you knew you'd need this registry.

**2. The AUC=0.5964 result has been independently reproduced at least 3 separate times, on 3 different commits, across 3 different dates** (`id=38` on 2026-07-26, `id=60` on 2026-07-30, `id=69` on 2026-08-10) — this is strong, registry-backed evidence of reproducibility, not a one-off lucky run.

**3. The liveness AUC=0.523 "honest negative" was consistent from the very first run** (`id=41`, 2026-07-27) through every subsequent rerun (`id=75, 84, 93, 101, 110` — always 0.5228) — demonstrating this wasn't a fluke or something that degraded over time; it's a stable, structural limitation of the LBP approach, exactly as your report frames it.

**4. The biometric ETL gap-closure is a directly visible, timestamped, three-step sequence** (`id=108` → 2 of 4 found → `id=109/110` → per-record persistence added → `id=111` → 4 of 4 found) — a real, auditable record of a gap being identified and closed, not a claim made after the fact.

---

## What this registry does NOT cover (honest scope note)

- **Model retraining/versioning beyond the single tuned model** — one tuned `isolation_forest_tuned.pkl` is reused throughout; this logs runs that used it, not multiple model version iterations.
- **The unexplained id 5–36 gap** — genuinely unknown cause, stated above rather than glossed over.
- **Manual/undocumented changes** — anything done without running a script that calls `log_provenance()` leaves no trace here.
- **Enforcement** — this document shows what happened; it does not yet block anything (e.g. failing a run if `git_dirty = t`). Combining this with the CI/CD pipeline is a natural next step, not yet implemented.
