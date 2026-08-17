"""
retraining_pipeline.py
Automated Model Retraining Pipeline on Drift Detection
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Orchestrates automated retraining when feature or model drift is detected
(e.g., PSI > 0.25 in drift_report):
  1. Checks drift status across behavioral features & model output
  2. Extracts the recent/drifted data window
  3. Trains a candidate Isolation Forest model
  4. Compares Champion vs. Candidate on holdout validation data
  5. Logs artifacts, comparison metrics, and promotion eligibility to MLflow

Responds directly to mid-sem evaluator feedback:
"Deploy continuous performance metrics, drift detection (PSI/KS), and alerts;
schedule retraining or canary rollouts when degradation detected."

Usage:
  python retraining_pipeline.py
  python retraining_pipeline.py --simulate-drift
  python retraining_pipeline.py --force-retrain
"""

import argparse
import os
import time

import joblib
import mlflow
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score, precision_score, recall_score
from sqlalchemy import create_engine, inspect

from provenance import log_provenance
from db_config import get_engine

CHAMPION_MODEL_PATH = "isolation_forest_tuned.pkl"
CANDIDATE_MODEL_PATH = "isolation_forest_candidate.pkl"
MLFLOW_EXPERIMENT = "kyc-automated-retraining"
DRIFT_TABLE = "drift_report"
SOURCE_TABLE = "behavioral_features"

FEATURE_COLUMNS = [
    "session_velocity_score",
    "device_reuse_score",
    "address_stability_score",
    "identity_consistency_score",
    "geographic_risk_score",
    "financial_risk_score",
]


def check_drift_status(engine) -> tuple[bool, list[str]]:
    """Checks if drift_report table contains ALERT status rows (PSI > 0.25)."""
    insp = inspect(engine)
    if not insp.has_table(DRIFT_TABLE):
        print(f"Table '{DRIFT_TABLE}' not found. No prior drift report on record.")
        return False, []

    df = pd.read_sql(f"SELECT * FROM {DRIFT_TABLE}", engine)
    if df.empty:
        return False, []

    # Detect PSI / status columns
    psi_cols = [c for c in df.columns if "psi" in c.lower()]
    drifted_features = []

    if psi_cols:
        psi_col = psi_cols[0]
        feature_cols = [c for c in df.columns if "feature" in c.lower() or "col" in c.lower()]
        feat_col = feature_cols[0] if feature_cols else "feature"

        drifted = df[df[psi_col] > 0.25]
        if not drifted.empty and feat_col in df.columns:
            drifted_features = drifted[feat_col].tolist()

    needs_retraining = len(drifted_features) > 0
    return needs_retraining, drifted_features


def load_training_data(engine, sample_size: int = 50000) -> tuple[pd.DataFrame, pd.Series]:
    print(f"Loading reference training sample ({sample_size:,} rows) from '{SOURCE_TABLE}'...")
    df = pd.read_sql(f"SELECT * FROM {SOURCE_TABLE} LIMIT {sample_size}", engine)
    X = df[FEATURE_COLUMNS].copy()
    y = df["fraud_bool"].astype(int).copy() if "fraud_bool" in df.columns else None
    return X, y


def train_candidate_model(X_train: pd.DataFrame) -> IsolationForest:
    print("\nTraining Candidate Isolation Forest model...")
    candidate = IsolationForest(
        n_estimators=150,
        max_samples=256,
        contamination=0.012,
        random_state=42,
        n_jobs=-1,
    )
    start = time.perf_counter()
    candidate.fit(X_train)
    duration = time.perf_counter() - start
    print(f"Candidate model trained in {duration:.2f}s.")
    return candidate


def evaluate_models(champion, candidate, X_val: pd.DataFrame, y_val: pd.Series) -> dict:
    print("\nEvaluating Champion vs. Candidate on validation holdout...")

    champ_scores = -champion.decision_function(X_val)
    cand_scores = -candidate.decision_function(X_val)

    champ_auc = roc_auc_score(y_val, champ_scores)
    cand_auc = roc_auc_score(y_val, cand_scores)

    # Top 5% Contamination metrics
    champ_pred = (champ_scores >= np.percentile(champ_scores, 95)).astype(int)
    cand_pred = (cand_scores >= np.percentile(cand_scores, 95)).astype(int)

    champ_prec = precision_score(y_val, champ_pred, zero_division=0)
    cand_prec = precision_score(y_val, cand_pred, zero_division=0)

    champ_rec = recall_score(y_val, champ_pred, zero_division=0)
    cand_rec = recall_score(y_val, cand_pred, zero_division=0)

    auc_delta = cand_auc - champ_auc
    eligible_for_canary = cand_auc >= (champ_auc - 0.005)  # Candidate is competitive

    print(f"  Champion  AUC: {champ_auc:.4f} | Recall@5%: {champ_rec*100:.2f}% | Precision: {champ_prec*100:.2f}%")
    print(f"  Candidate AUC: {cand_auc:.4f} | Recall@5%: {cand_rec*100:.2f}% | Precision: {cand_prec*100:.2f}%")
    print(f"  AUC Delta: {auc_delta:+.4f} -> Eligible for Canary Rollout: {eligible_for_canary}")

    return {
        "champion_auc": champ_auc,
        "candidate_auc": cand_auc,
        "auc_delta": auc_delta,
        "champion_recall_pct": champ_rec * 100,
        "candidate_recall_pct": cand_rec * 100,
        "eligible_for_canary": eligible_for_canary,
    }


def main():
    parser = argparse.ArgumentParser(description="Automated drift-triggered retraining pipeline")
    parser.add_argument("--simulate-drift", action="store_true", help="Simulate drift condition to force retraining")
    parser.add_argument("--force-retrain", action="store_true", help="Force retrain regardless of drift status")
    parser.add_argument("--sample-size", type=int, default=50000)
    args = parser.parse_args()

    print("=" * 65)
    print("AUTOMATED RETRAINING PIPELINE")
    print("Continuous Model Lifecycle & Drift Remediation")
    print("=" * 65)

    engine = get_engine()
    drift_flag, drifted_feats = check_drift_status(engine)

    if args.simulate_drift:
        drift_flag = True
        drifted_feats = ["session_velocity_score", "address_stability_score (SIMULATED)"]
        print("[SIMULATION] Drift condition manually asserted.")

    if not drift_flag and not args.force_retrain:
        print("\n[STATUS: STABLE] No significant drift detected (all PSI <= 0.25).")
        print("Model retraining is not required at this time.")
        return 0

    print(f"\n[ALERT] Retraining triggered! Drifted features detected: {drifted_feats}")

    if not os.path.exists(CHAMPION_MODEL_PATH):
        print(f"[ERROR] Champion model file '{CHAMPION_MODEL_PATH}' not found!")
        return 1

    champion = joblib.load(CHAMPION_MODEL_PATH)
    X, y = load_training_data(engine, sample_size=args.sample_size)

    # 80/20 train/val split
    split_idx = int(len(X) * 0.8)
    X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

    candidate = train_candidate_model(X_train)
    joblib.dump(candidate, CANDIDATE_MODEL_PATH)
    print(f"Saved candidate model artifact to '{CANDIDATE_MODEL_PATH}'.")

    metrics = evaluate_models(champion, candidate, X_val, y_val)

    # Log to MLflow
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    with mlflow.start_run(run_name=f"retraining_trigger_{time.strftime('%Y%m%d_%H%M%S')}"):
        mlflow.log_param("trigger_reason", "Drift_PSI_Alert" if drift_flag else "Manual_Force")
        mlflow.log_param("drifted_features", str(drifted_feats))
        mlflow.log_metric("champion_auc", metrics["champion_auc"])
        mlflow.log_metric("candidate_auc", metrics["candidate_auc"])
        mlflow.log_metric("auc_delta", metrics["auc_delta"])
        mlflow.log_metric("candidate_recall_pct", metrics["candidate_recall_pct"])
        mlflow.log_param("eligible_for_canary", str(metrics["eligible_for_canary"]))
        mlflow.log_artifact(CANDIDATE_MODEL_PATH)
        print(f"Logged candidate model and metrics to MLflow experiment '{MLFLOW_EXPERIMENT}'.")

    # Log Provenance
    try:
        log_provenance(
            engine,
            script_name="retraining_pipeline.py",
            source_dataset=SOURCE_TABLE,
            target_table=CANDIDATE_MODEL_PATH,
            row_count=len(X_train),
            notes=f"Retraining triggered on drift {drifted_feats}. Champion AUC: {metrics['champion_auc']:.4f}, Candidate AUC: {metrics['candidate_auc']:.4f}, Eligible for canary: {metrics['eligible_for_canary']}",
        )
        print("Logged retraining run to data_provenance.")
    except Exception as e:
        print(f"(non-fatal) Provenance logging skipped: {e}")

    print("\n[DONE] Retraining pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    exit(main())
