# Dataset & Model Change Registry
## Behavioral Observability Framework for KYC Onboarding
Pranali Pandharinath Supekar (2024DA04387)

This registry tracks every dataset ingestion, feature-engineering change,
and model version produced during the project, each traceable to a git
commit — satisfying the evaluator's requirement to "maintain a registry
for dataset/model updates."

**How to keep this current:** after any commit that changes a dataset,
feature set, or trained model, add one row below. Get the exact date with:
```
git log -1 --format=%ad --date=short <commit-hash>
```

---

## Dataset Changes

| # | Date | Dataset | Change | Rows | Git Commit | Notes |
|---|---|---|---|---|---|---|
| 1 | (mid-sem) | BAF Base (Bank Account Fraud) | Initial ingestion into `kyc_transactions` | 1,000,000 | `0134e95` | 33 columns (32 BAF fields + row_id) |
| 2 | (mid-sem) | BAF Base -> `behavioral_features` | Behavioral feature engineering (6 features + composite + synthetic biometric placeholders) | 1,000,000 | `0134e95` | 14 columns |
| 3 | (post-mid-sem) | `kyc_transactions` | Fixed ingestion chunksize (50k->1k) after PendingRollbackError | 1,000,000 | `fdf928f` | No data change, reliability fix |
| 4 | (post-mid-sem) | LFW (Labeled Faces in the Wild) | Face-matching validation dataset added | ~2,200 train pairs, ~1,000 test pairs | `7e7bf4f` | Auto-downloaded via scikit-learn, independent of BAF |
| 5 | (post-mid-sem) | Kaggle "Real and Fake Face Detection" | Liveness detection validation dataset added | 1,081 real + 960 fake | `68f1b2b` | Independent of BAF, no applicant-level link |
| 6 | (post-mid-sem) | `kyc-onboarding-events` (Kafka topic) | Real-time event stream introduced (sampled from `kyc_transactions`) | Variable (tested at 20, 200) | `f070a6e` | Simulated live stream, not new source data |

## Model Changes

| # | Date | Model | Change | Key Metric | Git Commit | Notes |
|---|---|---|---|---|---|---|
| 1 | (mid-sem) | Isolation Forest (baseline) | Initial baseline: `n_estimators=100, contamination=0.011, random_state=42` | AUC 0.5678, F1 2.42% | (mid-sem report) | Trained on 6 behavioral features, evaluated in Table 6 |
| 2 | (post-mid-sem) | Isolation Forest (tuned) | Optuna 10-trial search | AUC 0.5902, F1 3.62% | `869b2af` (superseded) | Intermediate result |
| 3 | (post-mid-sem) | Isolation Forest (tuned, final) | Optuna 30-trial search: `n_estimators` + `max_samples` + `contamination` + `max_features` tuned | AUC 0.5964, Recall 7.74%, TP 854 | `869b2af` | Current production model — `isolation_forest_tuned.pkl` |
| 4 | (post-mid-sem) | Face-matching model | PCA(150) + Logistic Regression on LFW pixel-difference features | AUC 0.6940 | `7e7bf4f` | `face_match_model.pkl` |
| 5 | (post-mid-sem) | Liveness detection model | LBP texture features + Random Forest | AUC 0.5228 | `68f1b2b` | `liveness_model.pkl` — known limitation, see dev log |

---

## Feature Set Version History

| Version | Features | Introduced | Notes |
|---|---|---|---|
| v1 (mid-sem) | session_velocity_score, device_reuse_score, address_stability_score, identity_consistency_score, geographic_risk_score, financial_risk_score, risk_anomaly_score (composite) | `0134e95` | Composite excluded from model input (Stage 2) — redundant with its own components |
| v1 + biometric (experimental) | v1 + liveness_score, face_match_score, ocr_confidence_score, biometric_risk_score, risk_anomaly_score_experimental_with_biometric | `0134e95` | Synthetic biometric columns generated FROM fraud_bool — explicitly flagged as label leakage, never used for reported model performance |

**Feature importance ranking (current model, from ablation + SHAP — see `feature_ablation.py` / `shap_explainability.py`):**
1. device_reuse_score (highest impact both methods)
2. address_stability_score / financial_risk_score
3. geographic_risk_score
4. session_velocity_score
5. identity_consistency_score (negligible contribution — flagged as limitation)

---

## Full Commit History Reference

For the complete, authoritative history (including infrastructure and
bugfix commits not listed above), run:
```
git log --oneline
```
in the project directory. This registry captures dataset/model-relevant
changes specifically; infrastructure commits (Docker, Kafka setup,
Prometheus/Grafana config) are tracked in git but not duplicated here
since they don't change data or model artifacts.
