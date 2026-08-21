"""
shap_explainability.py
Stage 7 -- Explainability (SHAP)
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Answers the question the mid-sem baseline couldn't: WHY did the model
flag a given record as anomalous? Uses SHAP's TreeExplainer (Isolation
Forest is tree-based, so this applies natively and is fast) to compute
per-feature attributions for a sample of flagged anomalies, plus a
global summary showing which features drive anomaly scores overall.

This is a different lens than Stage 2's ablation experiment: ablation
asks "how much does the model's AGGREGATE performance (AUC) depend on
each feature". SHAP asks "for THIS SPECIFIC flagged record, which
features pushed the score up". Both are useful; SHAP is what turns a
raw anomaly score into an analyst-readable explanation, e.g. "flagged
mainly due to device_reuse_score + financial_risk_score".

Usage:
    python shap_explainability.py --n-samples 500
    python shap_explainability.py --n-samples 500 --db-url postgresql://user:pass@localhost:5432/kyc_db

Requires:
    pip install shap matplotlib

DIAGNOSTIC BUILD: adds timing prints around each major step so a slow
run can be attributed to a specific stage (DB load, full-dataset scoring,
TreeExplainer construction, or the SHAP value computation itself) instead
of being a black box.
"""

import argparse
import os
import time

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
from sqlalchemy import create_engine, text

from provenance import log_provenance

DEFAULT_DB_URL = os.environ.get(
    "KYC_DB_URL", "postgresql://kyc_user:kyc_pass@localhost:5432/kyc_db"
)

SOURCE_TABLE = "behavioral_features"
TUNED_MODEL_PATH = "isolation_forest_tuned.pkl"
OUTPUT_TABLE = "shap_explanations"
SUMMARY_PLOT_PATH = "shap_summary_plot.png"

FEATURE_COLUMNS = [
    "session_velocity_score",
    "device_reuse_score",
    "address_stability_score",
    "identity_consistency_score",
    "geographic_risk_score",
    "financial_risk_score",
]
LABEL_COLUMN = "fraud_bool"

CREATE_OUTPUT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {OUTPUT_TABLE} (
    id SERIAL PRIMARY KEY,
    row_id BIGINT,
    fraud_bool INTEGER,
    anomaly_score DOUBLE PRECISION,
    top_driver_1 TEXT,
    top_driver_1_shap DOUBLE PRECISION,
    top_driver_2 TEXT,
    top_driver_2_shap DOUBLE PRECISION,
    top_driver_3 TEXT,
    top_driver_3_shap DOUBLE PRECISION,
    explained_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


def _t(label, start):
    elapsed = time.time() - start
    print(f"  [TIMING] {label}: {elapsed:.1f}s")


def load_data_and_model(db_url: str, n_samples: int):
    t0 = time.time()
    print(f"Loading tuned model from '{TUNED_MODEL_PATH}'...")
    model = joblib.load(TUNED_MODEL_PATH)
    _t("load model", t0)

    print(f"Loading '{SOURCE_TABLE}' and identifying flagged anomalies...")

    t0 = time.time()
    engine = create_engine(db_url)
    limit_clause = f"LIMIT {max(n_samples * 50, 25000)}" if (n_samples and n_samples < 50000) else ""
    df = pd.read_sql(f"SELECT * FROM {SOURCE_TABLE} {limit_clause}", engine)
    _t(f"SELECT * FROM {SOURCE_TABLE} ({len(df):,} rows)", t0)

    t0 = time.time()
    X_full = df[FEATURE_COLUMNS]
    predictions = model.predict(X_full)
    df["is_anomaly"] = predictions == -1
    _t(f"model.predict() on full {len(df):,}-row table", t0)

    anomalies = df[df["is_anomaly"]]
    print(f"Total flagged anomalies in dataset: {len(anomalies):,}")

    if n_samples is None or n_samples in (0, len(anomalies)):
        sample = anomalies
        print(f"Explaining ALL {len(anomalies):,} flagged anomalies across the dataset")
    else:
        sample_size = min(n_samples, len(anomalies))
        sample = anomalies.sample(n=sample_size, random_state=42)
        print(f"Explaining a sample of {sample_size:,} flagged anomalies")

    return model, engine, sample


def compute_shap_values(model, X: pd.DataFrame):
    print(f"Building SHAP TreeExplainer for {len(X):,} records "
          f"(Isolation Forest is tree-based, so this applies natively)...")
    t0 = time.time()
    explainer = shap.TreeExplainer(model)
    _t("build TreeExplainer (parses forest structure)", t0)

    t0 = time.time()
    shap_values = explainer.shap_values(X)
    _t(f"explainer.shap_values() on {len(X):,} records -- THIS IS USUALLY THE SLOW STEP", t0)
    return shap_values, explainer


def top_drivers_per_record(shap_values: np.ndarray, feature_names: list, k: int = 3) -> list[list[tuple]]:
    """For each row, returns the top-k (feature, shap_value) pairs by absolute magnitude."""
    results = []
    for row in shap_values:
        pairs = list(zip(feature_names, row))
        pairs.sort(key=lambda p: abs(p[1]), reverse=True)
        results.append(pairs[:k])
    return results


def write_explanations(engine, sample: pd.DataFrame, shap_values: np.ndarray,
                        model, feature_names: list):
    t0 = time.time()
    anomaly_scores = -model.score_samples(sample[feature_names])
    drivers = top_drivers_per_record(shap_values, feature_names, k=3)

    rows = []
    for (_, record), score, top3 in zip(sample.iterrows(), anomaly_scores, drivers):
        row = {
            "row_id": record.get("row_id"),
            "fraud_bool": int(record.get(LABEL_COLUMN)) if pd.notna(record.get(LABEL_COLUMN)) else None,
            "anomaly_score": float(score),
        }
        for i in range(3):
            if i < len(top3):
                row[f"top_driver_{i+1}"] = top3[i][0]
                row[f"top_driver_{i+1}_shap"] = float(top3[i][1])
            else:
                row[f"top_driver_{i+1}"] = None
                row[f"top_driver_{i+1}_shap"] = None
        rows.append(row)

    results_df = pd.DataFrame(rows)
    with engine.connect() as conn:
        conn.execute(text(CREATE_OUTPUT_TABLE_SQL))
        conn.commit()
    results_df.to_sql(OUTPUT_TABLE, engine, if_exists="replace", index=False, method="multi", chunksize=500)
    _t(f"write {len(results_df):,} explanations to DB", t0)
    print(f"Wrote {len(results_df)} per-record explanations to '{OUTPUT_TABLE}'")
    return results_df


def save_summary_plot(shap_values: np.ndarray, sample: pd.DataFrame, feature_names: list):
    print(f"Generating global SHAP summary plot...")
    t0 = time.time()
    plt.figure(figsize=(8, 5))
    shap.summary_plot(
        shap_values, sample[feature_names], feature_names=feature_names,
        plot_type="bar", show=False
    )
    plt.title("Global Feature Importance (mean |SHAP value|)\nWhy the model flags onboarding records as anomalous")
    plt.tight_layout()
    plt.savefig(SUMMARY_PLOT_PATH, dpi=150)
    plt.close()
    _t("generate + save summary plot", t0)
    print(f"Saved to '{SUMMARY_PLOT_PATH}'")


def print_example_explanations(results_df: pd.DataFrame, n: int = 5):
    print("\n" + "=" * 65)
    print(f"EXAMPLE EXPLANATIONS (first {n} of {len(results_df)} sampled records)")
    print("=" * 65)
    for _, row in results_df.head(n).iterrows():
        fraud_label = "ACTUAL FRAUD" if row["fraud_bool"] == 1 else "not fraud (per label)"
        print(f"\nrow_id={row['row_id']}  anomaly_score={row['anomaly_score']:.4f}  [{fraud_label}]")
        print(f"  Top driver 1: {row['top_driver_1']} (SHAP {row['top_driver_1_shap']:+.4f})")
        print(f"  Top driver 2: {row['top_driver_2']} (SHAP {row['top_driver_2_shap']:+.4f})")
        print(f"  Top driver 3: {row['top_driver_3']} (SHAP {row['top_driver_3_shap']:+.4f})")


def print_global_ranking(shap_values: np.ndarray, feature_names: list):
    mean_abs_shap = np.abs(shap_values).mean(axis=0)
    ranking = sorted(zip(feature_names, mean_abs_shap), key=lambda p: p[1], reverse=True)

    print("\n" + "=" * 65)
    print("GLOBAL FEATURE IMPORTANCE (mean |SHAP value| across sample)")
    print("=" * 65)
    print(f"{'Rank':<6}{'Feature':<32}{'Mean |SHAP|':<15}")
    for rank, (feat, val) in enumerate(ranking, start=1):
        print(f"{rank:<6}{feat:<32}{val:<15.4f}")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="Stage 7: SHAP explainability")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL)
    parser.add_argument("--n-samples", type=int, default=None,
                         help="Number of flagged anomalies to explain (default: None for all flagged anomalies)")
    args = parser.parse_args()

    run_start = time.time()

    print("=" * 65)
    print("STAGE 7 -- SHAP Explainability")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    model, engine, sample = load_data_and_model(args.db_url, args.n_samples)
    shap_values, explainer = compute_shap_values(model, sample[FEATURE_COLUMNS])

    results_df = write_explanations(engine, sample, shap_values, model, FEATURE_COLUMNS)
    save_summary_plot(shap_values, sample, FEATURE_COLUMNS)
    print_global_ranking(shap_values, FEATURE_COLUMNS)
    print_example_explanations(results_df)

    log_provenance(
        engine,
        script_name="shap_explainability.py",
        source_dataset=SOURCE_TABLE,
        target_table=OUTPUT_TABLE,
        row_count=len(results_df),
        notes=f"SHAP TreeExplainer, {len(results_df)} flagged anomalies explained",
    )

    print(f"\n[TOTAL RUNTIME] {(time.time() - run_start):.1f}s")
    print("\n[DONE] SHAP explanations complete. "
          f"See '{OUTPUT_TABLE}' table and '{SUMMARY_PLOT_PATH}' for report figures.")


if __name__ == "__main__":
    main()
