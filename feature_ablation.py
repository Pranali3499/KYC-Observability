"""
feature_ablation.py
Stage 2 -- Feature-Importance / Ablation Experiment
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

For each of the 6 behavioral features, retrains the Isolation Forest
with that ONE feature removed and measures the AUC drop versus the
full-feature model. A feature whose removal barely changes AUC is
doing little work; a feature whose removal causes a big drop is a
key driver of the model's fraud-detection signal.

Uses the best hyperparameters found by mlflow_optuna_tuning.py
(Stage 2 tuning) as the fixed model config -- ablation isolates the
effect of FEATURES, not hyperparameters, so those are held constant
across every run here.

Usage:
    python feature_ablation.py
    python feature_ablation.py --db-url postgresql://user:pass@localhost:5432/kyc_db

Requires:
    pip install mlflow scikit-learn joblib
"""

import argparse
import os

import joblib
import mlflow
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sqlalchemy import create_engine

from provenance import log_provenance

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DB_URL = os.environ.get(
    "KYC_DB_URL", "postgresql://kyc_user:kyc_pass@localhost:5432/kyc_db"
)

SOURCE_TABLE = "behavioral_features"

FEATURE_COLUMNS = [
    "session_velocity_score",
    "device_reuse_score",
    "address_stability_score",
    "identity_consistency_score",
    "geographic_risk_score",
    "financial_risk_score",
]
LABEL_COLUMN = "fraud_bool"

TUNED_MODEL_PATH = "isolation_forest_tuned.pkl"
MLFLOW_EXPERIMENT_NAME = "kyc-behavioral-observability"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_data(engine) -> tuple[pd.DataFrame, pd.Series]:
    print(f"Loading '{SOURCE_TABLE}' ...")
    df = pd.read_sql(f"SELECT * FROM {SOURCE_TABLE}", engine)
    X = df[FEATURE_COLUMNS].copy()
    y = df[LABEL_COLUMN].astype(int).copy()
    print(f"Loaded {len(df):,} rows")
    return X, y


def get_fixed_params() -> dict:
    """
    Pulls hyperparameters from the already-tuned model (Stage 2 tuning
    output) so ablation holds them constant -- isolating the effect of
    each FEATURE rather than conflating it with hyperparameter choice.
    Falls back to the mid-sem baseline params if the tuned model isn't
    found (e.g. ablation run before tuning).
    """
    if os.path.exists(TUNED_MODEL_PATH):
        tuned_model = joblib.load(TUNED_MODEL_PATH)
        params = {
            "n_estimators": tuned_model.n_estimators,
            "max_samples": tuned_model.max_samples,
            "contamination": tuned_model.contamination,
            "max_features": tuned_model.max_features,
            "random_state": 42,
            "n_jobs": -1,
        }
        print(f"Using tuned hyperparameters from '{TUNED_MODEL_PATH}': {params}")
    else:
        params = {
            "n_estimators": 100,
            "contamination": 0.011,
            "random_state": 42,
            "n_jobs": -1,
        }
        print(f"'{TUNED_MODEL_PATH}' not found -- falling back to mid-sem baseline params: {params}")
    return params


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(model: IsolationForest, X: pd.DataFrame, y: pd.Series) -> dict:
    anomaly_score = -model.score_samples(X)
    auc = roc_auc_score(y, anomaly_score)
    y_pred = (model.predict(X) == -1).astype(int)
    return {
        "auc": auc,
        "precision": precision_score(y, y_pred, zero_division=0),
        "recall": recall_score(y, y_pred, zero_division=0),
        "f1": f1_score(y, y_pred, zero_division=0),
    }


def fit_and_evaluate(X: pd.DataFrame, y: pd.Series, params: dict) -> dict:
    model = IsolationForest(**params)
    model.fit(X)
    return evaluate(model, X, y)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stage 2: Feature-importance ablation experiment")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL)
    args = parser.parse_args()

    print("=" * 65)
    print("STAGE 2 -- Feature-Importance / Ablation Experiment")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    engine = create_engine(args.db_url)
    X, y = load_data(engine)
    params = get_fixed_params()

    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    results = []

    # --- Full-feature baseline for this experiment ---
    print("\n[1/7] Full feature set (baseline for ablation)...")
    with mlflow.start_run(run_name="ablation_full_features"):
        mlflow.log_params(params)
        mlflow.log_param("features_used", ",".join(FEATURE_COLUMNS))
        full_metrics = fit_and_evaluate(X, y, params)
        mlflow.log_metrics(full_metrics)
    print(f"  AUC = {full_metrics['auc']:.4f}")
    results.append({"removed_feature": "(none -- full set)", **full_metrics, "auc_drop": 0.0})

    # --- Leave-one-out runs ---
    for i, feature in enumerate(FEATURE_COLUMNS, start=1):
        print(f"\n[{i+1}/7] Removing '{feature}'...")
        remaining = [c for c in FEATURE_COLUMNS if c != feature]
        with mlflow.start_run(run_name=f"ablation_without_{feature}"):
            mlflow.log_params(params)
            mlflow.log_param("features_used", ",".join(remaining))
            mlflow.log_param("removed_feature", feature)
            metrics = fit_and_evaluate(X[remaining], y, params)
            auc_drop = full_metrics["auc"] - metrics["auc"]
            mlflow.log_metrics(metrics)
            mlflow.log_metric("auc_drop", auc_drop)
        print(f"  AUC = {metrics['auc']:.4f}  (drop from full: {auc_drop:+.4f})")
        results.append({"removed_feature": feature, **metrics, "auc_drop": auc_drop})

    log_provenance(
        engine,
        script_name="feature_ablation.py",
        source_dataset=SOURCE_TABLE,
        target_table="ablation_results",
        row_count=len(X),
        notes=f"{len(FEATURE_COLUMNS)}-feature leave-one-out ablation",
    )

    # --- Ranked summary ---
    results_df = pd.DataFrame(results)
    ranked = results_df[results_df["removed_feature"] != "(none -- full set)"].sort_values(
        "auc_drop", ascending=False
    )

    print("\n" + "=" * 65)
    print("FEATURE IMPORTANCE (ranked by AUC drop when removed)")
    print("=" * 65)
    print(f"{'Rank':<6}{'Feature':<32}{'AUC w/o feature':<18}{'AUC drop':<12}")
    for rank, (_, row) in enumerate(ranked.iterrows(), start=1):
        print(f"{rank:<6}{row['removed_feature']:<32}{row['auc']:<18.4f}{row['auc_drop']:+.4f}")
    print("=" * 65)
    print(f"\nFull-feature AUC: {full_metrics['auc']:.4f}")
    print("Larger positive AUC drop = more important feature (removing it hurt the most).")
    print("A negative or near-zero drop means that feature contributes little, or the")
    print("model does marginally BETTER without it -- worth flagging in your report.")

    results_df.to_csv("ablation_results.csv", index=False)
    print("\nSaved full results to 'ablation_results.csv'")
    print("Run 'mlflow ui' to browse all runs in the MLflow UI.")


if __name__ == "__main__":
    main()
