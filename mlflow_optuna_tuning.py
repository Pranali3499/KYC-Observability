"""
mlflow_optuna_tuning.py
Stage 2 -- Model Experimentation & Tracking
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Runs an Optuna hyperparameter search over the Isolation Forest,
logging every trial (params + metrics) to MLflow. The best trial is
retrained, persisted to disk, and compared against the mid-sem
baseline from Table 4 of the report (n_estimators=100,
contamination=0.011, random_state=42).

Why AUC as the primary tuning objective, not precision/recall:
Isolation Forest's default predict() uses a contamination-based
threshold to output a hard anomaly/normal label -- but the underlying
anomaly SCORE is continuous. AUC evaluates that continuous score
across all thresholds at once, so it doesn't conflate "is this model
good at ranking risk" with "did we happen to pick a good cutoff."
That matches the acceptance-criteria discussion: AUC is the
threshold-independent, more defensible metric; precision/recall/F1
are still reported per trial for continuity with your original
baseline table, but AUC drives which trial "wins".

Usage:
    python mlflow_optuna_tuning.py --n-trials 30
    python mlflow_optuna_tuning.py --n-trials 30 --db-url postgresql://user:pass@localhost:5432/kyc_db

Requires:
    pip install mlflow optuna scikit-learn joblib
"""

import argparse
import os

import joblib
import mlflow
import numpy as np
import optuna
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sqlalchemy import create_engine

from provenance import log_provenance

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DB_URL = os.environ.get(
    "KYC_DB_URL", "postgresql://kyc_user:kyc_pass@localhost:5432/kyc_db"
)

SOURCE_TABLE = "behavioral_features"

# The 6 engineered behavioral scores -- deliberately excludes
# risk_anomaly_score (the composite) since it's a fixed linear
# combination of these same 6 columns. Feeding both the parts and
# their own weighted sum into the model would double-count signal
# and isn't a genuine 7th feature.
FEATURE_COLUMNS = [
    "session_velocity_score",
    "device_reuse_score",
    "address_stability_score",
    "identity_consistency_score",
    "geographic_risk_score",
    "financial_risk_score",
]
LABEL_COLUMN = "fraud_bool"  # ground truth, NEVER used as a model input

MODEL_OUTPUT_PATH = "isolation_forest_tuned.pkl"
MLFLOW_EXPERIMENT_NAME = "kyc-behavioral-observability"

# Mid-sem baseline (report Table 4) -- used for the final comparison print.
BASELINE_PARAMS = {"n_estimators": 100, "contamination": 0.011, "random_state": 42}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_data(engine) -> tuple[pd.DataFrame, pd.Series]:
    print(f"Loading '{SOURCE_TABLE}' ...")
    df = pd.read_sql(f"SELECT * FROM {SOURCE_TABLE}", engine)
    print(f"Loaded {len(df):,} rows")

    missing = [c for c in FEATURE_COLUMNS + [LABEL_COLUMN] if c not in df.columns]
    if missing:
        raise ValueError(f"Expected columns not found in {SOURCE_TABLE}: {missing}")

    X = df[FEATURE_COLUMNS].copy()
    y = df[LABEL_COLUMN].astype(int).copy()
    return X, y


# ---------------------------------------------------------------------------
# Model evaluation
# ---------------------------------------------------------------------------

def evaluate(model: IsolationForest, X: pd.DataFrame, y: pd.Series) -> dict:
    """
    Returns AUC (threshold-independent, on the continuous anomaly score)
    plus precision/recall/F1 (threshold-dependent, using the model's own
    contamination-based predict() -- matches how the mid-sem baseline
    was evaluated in Table 6 of the report).
    """
    # score_samples: higher = more "normal" (inlier). Negate so higher =
    # more anomalous = more fraud-like, which is what roc_auc_score expects
    # for the positive class.
    anomaly_score = -model.score_samples(X)
    auc = roc_auc_score(y, anomaly_score)

    raw_pred = model.predict(X)  # -1 = anomaly, 1 = normal
    y_pred = (raw_pred == -1).astype(int)  # 1 = predicted fraud

    precision = precision_score(y, y_pred, zero_division=0)
    recall = recall_score(y, y_pred, zero_division=0)
    f1 = f1_score(y, y_pred, zero_division=0)
    anomalies_flagged = int(y_pred.sum())
    true_positives = int(((y_pred == 1) & (y == 1)).sum())

    return {
        "auc": auc,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "anomalies_flagged": anomalies_flagged,
        "true_positives": true_positives,
    }


# ---------------------------------------------------------------------------
# Optuna objective
# ---------------------------------------------------------------------------

def make_objective(X: pd.DataFrame, y: pd.Series):
    def objective(trial: optuna.Trial) -> float:
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 50, 300, step=10),
            "max_samples": trial.suggest_float("max_samples", 0.2, 1.0),
            "contamination": trial.suggest_float("contamination", 0.005, 0.05, log=True),
            "max_features": trial.suggest_float("max_features", 0.5, 1.0),
            "random_state": 42,
            "n_jobs": -1,
        }

        with mlflow.start_run(nested=True):
            mlflow.log_params(params)

            model = IsolationForest(**params)
            model.fit(X)
            metrics = evaluate(model, X, y)

            mlflow.log_metrics(metrics)

        trial.set_user_attr("metrics", metrics)
        return metrics["auc"]

    return objective


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stage 2: Optuna + MLflow hyperparameter tuning")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL)
    parser.add_argument("--n-trials", type=int, default=30)
    args = parser.parse_args()

    print("=" * 65)
    print("STAGE 2 -- MLflow + Optuna Hyperparameter Tuning")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    engine = create_engine(args.db_url)
    X, y = load_data(engine)

    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    # --- Baseline run, logged for direct comparison in MLflow's UI ---
    print("\n[1/3] Evaluating mid-sem baseline (Table 4 params)...")
    with mlflow.start_run(run_name="baseline_midsem"):
        mlflow.log_params(BASELINE_PARAMS)
        baseline_model = IsolationForest(**BASELINE_PARAMS, n_jobs=-1)
        baseline_model.fit(X)
        baseline_metrics = evaluate(baseline_model, X, y)
        mlflow.log_metrics(baseline_metrics)
    print(f"  Baseline AUC: {baseline_metrics['auc']:.4f}  "
          f"Precision: {baseline_metrics['precision']:.4f}  "
          f"Recall: {baseline_metrics['recall']:.4f}  "
          f"F1: {baseline_metrics['f1']:.4f}")

    # --- Optuna search ---
    print(f"\n[2/3] Running Optuna search ({args.n_trials} trials, objective = AUC)...")
    with mlflow.start_run(run_name="optuna_search_parent"):
        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(make_objective(X, y), n_trials=args.n_trials, show_progress_bar=False)

        mlflow.log_param("n_trials", args.n_trials)
        mlflow.log_metric("best_auc", study.best_value)

    best_params = study.best_params
    best_params["random_state"] = 42
    best_params["n_jobs"] = -1
    best_metrics = study.best_trial.user_attrs["metrics"]

    print(f"  Best trial: AUC = {study.best_value:.4f}")
    print(f"  Best params: {study.best_params}")

    # --- Retrain and persist the best model ---
    print("\n[3/3] Retraining best model on full data and persisting...")
    with mlflow.start_run(run_name="best_model_final"):
        mlflow.log_params(best_params)
        best_model = IsolationForest(**best_params)
        best_model.fit(X)
        final_metrics = evaluate(best_model, X, y)
        mlflow.log_metrics(final_metrics)
        mlflow.sklearn.log_model(best_model, "model")

    joblib.dump(best_model, MODEL_OUTPUT_PATH)
    print(f"  Saved tuned model to '{MODEL_OUTPUT_PATH}'")

    log_provenance(
        engine,
        script_name="mlflow_optuna_tuning.py",
        source_dataset=SOURCE_TABLE,
        target_table=MODEL_OUTPUT_PATH,
        row_count=len(X),
        notes=f"Optuna best AUC={study.best_value:.4f}, {args.n_trials} trials",
    )

    # --- Comparison summary ---
    print("\n" + "=" * 65)
    print("BASELINE vs TUNED MODEL")
    print("=" * 65)
    print(f"{'Metric':<20}{'Baseline (Table 4)':<22}{'Tuned':<15}")
    for key in ("auc", "precision", "recall", "f1"):
        print(f"{key.upper():<20}{baseline_metrics[key]:<22.4f}{final_metrics[key]:<15.4f}")
    print(f"{'Anomalies flagged':<20}{baseline_metrics['anomalies_flagged']:<22}"
          f"{final_metrics['anomalies_flagged']:<15}")
    print(f"{'True positives':<20}{baseline_metrics['true_positives']:<22}"
          f"{final_metrics['true_positives']:<15}")
    print("=" * 65)
    print("\nRun 'mlflow ui' in this folder to browse all trials in the MLflow UI.")


if __name__ == "__main__":
    main()
