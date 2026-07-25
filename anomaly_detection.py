"""
anomaly_detection.py
Demo Piece 3 -- Baseline Isolation Forest Anomaly Detection

Trains an unsupervised Isolation Forest on the engineered
behavioral features and writes anomaly scores/flags back to
PostgreSQL. Evaluates against the (unused-in-training) fraud_bool
ground truth for reporting purposes only.

Usage:
    python anomaly_detection.py
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import confusion_matrix, precision_score, recall_score, f1_score, accuracy_score
from sqlalchemy import text
from db_config import get_engine

SOURCE_TABLE = "behavioral_features"
TARGET_TABLE = "anomaly_scores"

FEATURE_COLS = [
    "session_velocity_score",
    "device_reuse_score",
    "address_stability_score",
    "identity_consistency_score",
    "geographic_risk_score",
    "financial_risk_score",
    "risk_anomaly_score",
]
# NOTE: biometric_risk_score / liveness_score / face_match_score /
# ocr_confidence_score / risk_anomaly_score_experimental_with_biometric
# are DELIBERATELY excluded from the official model. They are
# synthetically generated from fraud_bool itself (see
# biometric_features.py), so including them + evaluating against
# fraud_bool causes label leakage and artificially inflated
# precision/recall. Keep this list behavioral-only for anything you
# report as your baseline.

N_ESTIMATORS = 100
CONTAMINATION = 0.011  # matches BAF's ~1.1% fraud prevalence
RANDOM_STATE = 42


def load_features(engine) -> pd.DataFrame:
    print(f"Reading engineered features from '{SOURCE_TABLE}' ...")
    df = pd.read_sql(f"SELECT * FROM {SOURCE_TABLE}", engine)
    print(f"Loaded {len(df):,} rows")
    return df


def run_isolation_forest(df: pd.DataFrame) -> pd.DataFrame:
    print("Running Isolation Forest...")
    print(f"Parameters: n_estimators={N_ESTIMATORS}, contamination={CONTAMINATION}, random_state={RANDOM_STATE}")

    X = df[FEATURE_COLS].to_numpy()

    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination=CONTAMINATION,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    model.fit(X)

    raw_scores = model.decision_function(X)          # higher = more normal
    df["risk_anomaly_score_model"] = -raw_scores       # flip so higher = more anomalous
    df["flagged_anomaly"] = (model.predict(X) == -1).astype(int)

    n_flagged = df["flagged_anomaly"].sum()
    print(f"Anomalies flagged: {n_flagged:,}")
    return df


def evaluate(df: pd.DataFrame):
    if "fraud_bool" not in df.columns:
        print("(no fraud_bool column available -- skipping evaluation)")
        return

    y_true = df["fraud_bool"].astype(int)
    y_pred = df["flagged_anomaly"].astype(int)

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    acc = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)

    print("Evaluation against fraud labels:")
    print(f"  True Positives : {tp:,}")
    print(f"  False Positives: {fp:,}")
    print(f"  True Negatives : {tn:,}")
    print(f"  False Negatives: {fn:,}")
    print(f"  Accuracy : {acc*100:.2f}%")
    print(f"  Precision: {prec*100:.2f}%")
    print(f"  Recall   : {rec*100:.2f}%")
    print(f"  F1-Score : {f1*100:.2f}%")


def write_results(df: pd.DataFrame, engine):
    print(f"Writing anomaly scores to '{TARGET_TABLE}' ...")
    df.to_sql(TARGET_TABLE, engine, if_exists="replace", index=False, chunksize=50_000, method="multi")
    with engine.connect() as conn:
        conn.execute(text(f'ALTER TABLE {TARGET_TABLE} ADD COLUMN IF NOT EXISTS row_id SERIAL PRIMARY KEY;'))
        conn.commit()


def main():
    print("=" * 65)
    print("DEMO PIECE 3 -- Isolation Forest Anomaly Detection")
    print("=" * 65)

    engine = get_engine()
    df = load_features(engine)
    df = run_isolation_forest(df)
    evaluate(df)
    write_results(df, engine)

    print("[demo3] PASS -- Baseline anomaly detection completed.")


if __name__ == "__main__":
    main()
