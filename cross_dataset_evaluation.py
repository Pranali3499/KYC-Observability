"""
cross_dataset_evaluation.py
Model Generalization & Cross-Dataset Validation
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Evaluates the tuned Isolation Forest model on alternative datasets
(Base.csv and Variant I through Variant V of the Bank Account Fraud suite)
to measure generalization, model robustness, and performance degradation
under distribution shift.

Responds directly to mid-sem evaluator feedback:
"Evaluate model on alternative datasets, run cross-dataset ROC/AUC,
measure generalization and dataset shift."

Outputs:
  - cross_dataset_summary.csv: Table of metrics (AUC, FAR, FRR, Detection Rate, PSI) per variant
  - cross_dataset_roc_curves.png: Multi-variant overlay ROC curves
  - MLflow logging under experiment 'kyc-cross-dataset-validation'
  - Provenance entry in data_provenance table

Usage:
  python cross_dataset_evaluation.py
  python cross_dataset_evaluation.py --sample-size 50000
  python cross_dataset_evaluation.py --full
"""

import argparse
import os
import time

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, roc_curve

from provenance import log_provenance
from db_config import get_engine

TUNED_MODEL_PATH = "isolation_forest_tuned.pkl"
MLFLOW_EXPERIMENT = "kyc-cross-dataset-validation"
SUMMARY_CSV_PATH = "cross_dataset_summary.csv"
ROC_PLOT_PATH = "cross_dataset_roc_curves.png"

FEATURE_COLUMNS = [
    "session_velocity_score",
    "device_reuse_score",
    "address_stability_score",
    "identity_consistency_score",
    "geographic_risk_score",
    "financial_risk_score",
]

SENTINEL_COLS = [
    "prev_address_months_count",
    "current_address_months_count",
    "bank_months_count",
    "session_length_in_minutes",
    "device_distinct_emails_8w",
]

COLS = {
    "velocity_6h": "velocity_6h",
    "velocity_24h": "velocity_24h",
    "velocity_4w": "velocity_4w",
    "device_distinct_emails_8w": "device_distinct_emails_8w",
    "device_fraud_count": "device_fraud_count",
    "prev_address_months_count": "prev_address_months_count",
    "current_address_months_count": "current_address_months_count",
    "name_email_similarity": "name_email_similarity",
    "phone_home_valid": "phone_home_valid",
    "phone_mobile_valid": "phone_mobile_valid",
    "foreign_request": "foreign_request",
    "source": "source",
    "income": "income",
    "credit_risk_score": "credit_risk_score",
    "proposed_credit_limit": "proposed_credit_limit",
    "bank_months_count": "bank_months_count",
    "fraud_bool": "fraud_bool",
}

DATASET_FILES = {
    "Base": "Base.csv",
    "Variant I": "Variant I.csv",
    "Variant II": "Variant II.csv",
    "Variant III": "Variant III.csv",
    "Variant IV": "Variant IV.csv",
    "Variant V": "Variant V.csv",
}


def _minmax(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if hi - lo == 0:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - lo) / (hi - lo)


def clean_sentinels(df: pd.DataFrame) -> pd.DataFrame:
    for col in SENTINEL_COLS:
        if col in df.columns:
            n_sentinel = (df[col] == -1).sum()
            if n_sentinel:
                df[col] = df[col].replace(-1, np.nan)
                median_val = df[col].median()
                df[col] = df[col].fillna(median_val)
    return df


def engineer_features_from_df(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    # 1. Session Velocity Score
    out["session_velocity_score"] = _minmax(
        0.5 * df[COLS["velocity_6h"]].fillna(0)
        + 0.3 * df[COLS["velocity_24h"]].fillna(0)
        + 0.2 * df[COLS["velocity_4w"]].fillna(0)
    )

    # 2. Device Reuse Score
    out["device_reuse_score"] = _minmax(
        df[COLS["device_distinct_emails_8w"]].fillna(0)
        + 5 * df[COLS["device_fraud_count"]].fillna(0)
    )

    # 3. Address Stability Score
    addr_tenure = df[COLS["current_address_months_count"]].clip(lower=0).fillna(0)
    out["address_stability_score"] = _minmax(addr_tenure)

    # 4. Identity Consistency Score
    out["identity_consistency_score"] = _minmax(
        df[COLS["name_email_similarity"]].fillna(0)
        + df[COLS["phone_home_valid"]].fillna(0)
        + df[COLS["phone_mobile_valid"]].fillna(0)
    )

    # 5. Geographic Risk Score
    src_is_risky = (df[COLS["source"]] == "TELEAPP").astype(int) if COLS["source"] in df.columns else 0
    out["geographic_risk_score"] = _minmax(
        df[COLS["foreign_request"]].fillna(0).astype(float) + src_is_risky
    )

    # 6. Financial Risk Score
    out["financial_risk_score"] = _minmax(
        df[COLS["credit_risk_score"]].fillna(0)
        + df[COLS["proposed_credit_limit"]].fillna(0) / (df[COLS["income"]].replace(0, np.nan).fillna(1))
    )

    if COLS["fraud_bool"] in df.columns:
        out["fraud_bool"] = df[COLS["fraud_bool"]].astype(int)

    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.fillna(out.median(numeric_only=True))
    return out


def calculate_psi(ref: np.ndarray, curr: np.ndarray, num_bins: int = 10) -> float:
    """Calculates Population Stability Index between reference and current distribution."""
    ref = ref[~np.isnan(ref)]
    curr = curr[~np.isnan(curr)]
    if len(ref) == 0 or len(curr) == 0:
        return 0.0

    quantiles = np.linspace(0, 100, num_bins + 1)
    bin_edges = np.percentile(ref, quantiles)
    bin_edges[0] = -np.inf
    bin_edges[-1] = np.inf

    ref_counts, _ = np.histogram(ref, bins=bin_edges)
    curr_counts, _ = np.histogram(curr, bins=bin_edges)

    ref_pct = (ref_counts + 1e-4) / (len(ref) + 1e-4 * num_bins)
    curr_pct = (curr_counts + 1e-4) / (len(curr) + 1e-4 * num_bins)

    psi = np.sum((curr_pct - ref_pct) * np.log(curr_pct / ref_pct))
    return float(psi)


def evaluate_dataset(name: str, file_path: str, model, sample_size: int = None, ref_features: pd.DataFrame = None) -> dict:
    print(f"\n--- Processing {name} ({file_path}) ---")
    start = time.perf_counter()

    if not os.path.exists(file_path):
        print(f"  [ERROR] File not found: {file_path}")
        return None

    if sample_size:
        print(f"  Sampling {sample_size:,} rows...")
        df = pd.read_csv(file_path, nrows=sample_size)
    else:
        print(f"  Reading full dataset...")
        df = pd.read_csv(file_path)

    total_rows = len(df)
    fraud_count = int(df["fraud_bool"].sum()) if "fraud_bool" in df.columns else 0
    fraud_rate = (fraud_count / total_rows) * 100 if total_rows > 0 else 0
    print(f"  Loaded {total_rows:,} rows (Fraud: {fraud_count:,}, {fraud_rate:.2f}%)")

    df = clean_sentinels(df)
    features = engineer_features_from_df(df)

    X = features[FEATURE_COLUMNS]
    y_true = features["fraud_bool"].values if "fraud_bool" in features.columns else None

    # Score with isolation forest model
    anomaly_scores = -model.decision_function(X)
    is_anomaly = (model.predict(X) == -1).astype(int)

    # Metrics
    auc = float(roc_auc_score(y_true, anomaly_scores)) if y_true is not None and len(np.unique(y_true)) > 1 else None

    # Detection Rate @ Top 5% Contamination
    top_5_pct_threshold = np.percentile(anomaly_scores, 95)
    flagged_top_5 = (anomaly_scores >= top_5_pct_threshold).astype(int)
    tp = int(np.sum((flagged_top_5 == 1) & (y_true == 1))) if y_true is not None else 0
    fp = int(np.sum((flagged_top_5 == 1) & (y_true == 0))) if y_true is not None else 0
    tn = int(np.sum((flagged_top_5 == 0) & (y_true == 0))) if y_true is not None else 0
    fn = int(np.sum((flagged_top_5 == 0) & (y_true == 1))) if y_true is not None else 0

    detection_rate = (tp / fraud_count * 100) if fraud_count > 0 else 0
    fpr = (fp / (fp + tn) * 100) if (fp + tn) > 0 else 0

    # Model Output PSI relative to Base reference
    model_psi = 0.0
    if ref_features is not None and "anomaly_score" in ref_features:
        model_psi = calculate_psi(ref_features["anomaly_score"].values, anomaly_scores)

    # Feature-level PSI
    feature_psis = {}
    if ref_features is not None:
        for col in FEATURE_COLUMNS:
            feature_psis[col] = calculate_psi(ref_features[col].values, features[col].values)

    duration = time.perf_counter() - start
    print(f"  AUC: {auc:.4f} | Detection Rate @ top 5%: {detection_rate:.2f}% | Model Output PSI: {model_psi:.4f} | Time: {duration:.2f}s")

    features["anomaly_score"] = anomaly_scores
    features["is_anomaly"] = is_anomaly

    fpr_curve, tpr_curve, _ = roc_curve(y_true, anomaly_scores) if y_true is not None else (None, None, None)

    return {
        "dataset_name": name,
        "total_rows": total_rows,
        "fraud_count": fraud_count,
        "fraud_rate_pct": round(fraud_rate, 3),
        "auc": round(auc, 4) if auc is not None else None,
        "detection_rate_pct": round(detection_rate, 2),
        "false_positive_rate_pct": round(fpr, 2),
        "model_output_psi": round(model_psi, 4),
        "fpr_curve": fpr_curve,
        "tpr_curve": tpr_curve,
        "features_df": features,
        "feature_psis": feature_psis,
    }


def main():
    parser = argparse.ArgumentParser(description="Cross-dataset evaluation of KYC Isolation Forest model")
    parser.add_argument("--sample-size", type=int, default=50000,
                        help="Sample size per variant (default 50,000 for fast reproducible evaluation, 0 for full)")
    parser.add_argument("--full", action="store_true", help="Evaluate full datasets without sampling")
    args = parser.parse_args()

    sample_size = None if args.full or args.sample_size == 0 else args.sample_size

    print("=" * 70)
    print("CROSS-DATASET GENERALIZATION & SHIFT EVALUATION")
    print("Evaluating trained model on Base and Variant I through Variant V")
    print("=" * 70)

    if not os.path.exists(TUNED_MODEL_PATH):
        print(f"[ERROR] Model file '{TUNED_MODEL_PATH}' not found!")
        return 1

    print(f"Loading tuned model from '{TUNED_MODEL_PATH}'...")
    model = joblib.load(TUNED_MODEL_PATH)

    results = []
    roc_data = {}
    base_ref_features = None

    # Step 1: Evaluate Base first to establish reference
    base_res = evaluate_dataset("Base (Reference)", DATASET_FILES["Base"], model, sample_size=sample_size)
    if base_res:
        results.append(base_res)
        base_ref_features = base_res["features_df"]
        roc_data["Base (Reference)"] = (base_res["fpr_curve"], base_res["tpr_curve"], base_res["auc"])

    # Step 2: Evaluate Variant I through V
    for name, filename in DATASET_FILES.items():
        if name == "Base":
            continue
        res = evaluate_dataset(name, filename, model, sample_size=sample_size, ref_features=base_ref_features)
        if res:
            results.append(res)
            roc_data[name] = (res["fpr_curve"], res["tpr_curve"], res["auc"])

    # Step 3: Save Summary Table
    summary_rows = []
    for r in results:
        summary_rows.append({
            "Dataset": r["dataset_name"],
            "Rows Evaluated": r["total_rows"],
            "Fraud Count": r["fraud_count"],
            "Fraud Rate (%)": r["fraud_rate_pct"],
            "ROC-AUC": r["auc"],
            "Detection Rate @ 5% (%)": r["detection_rate_pct"],
            "FPR (%)": r["false_positive_rate_pct"],
            "Model Output PSI": r["model_output_psi"],
        })

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(SUMMARY_CSV_PATH, index=False)
    print("\n" + "=" * 70)
    print("CROSS-DATASET EVALUATION SUMMARY")
    print("=" * 70)
    print(summary_df.to_string(index=False))
    print(f"\nSummary table saved to '{SUMMARY_CSV_PATH}'")

    # Step 4: Plot Multi-Variant ROC Curves
    plt.figure(figsize=(9, 7))
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
    for i, (dname, (fpr, tpr, auc_val)) in enumerate(roc_data.items()):
        if fpr is not None and tpr is not None:
            plt.plot(fpr, tpr, label=f"{dname} (AUC = {auc_val:.4f})", color=colors[i % len(colors)], lw=2)

    plt.plot([0, 1], [0, 1], "k--", lw=1.5, alpha=0.7, label="Random Guessing (AUC = 0.5000)")
    plt.xlim([0.0, 1.0])
    plt.ylim([0.0, 1.05])
    plt.xlabel("False Positive Rate (1 - Specificity)", fontsize=11)
    plt.ylabel("True Positive Rate (Sensitivity / Recall)", fontsize=11)
    plt.title("Cross-Dataset Model Generalization: ROC Curves Across BAF Variants", fontsize=13, fontweight="bold")
    plt.legend(loc="lower right", fontsize=10)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(ROC_PLOT_PATH, dpi=200)
    plt.close()
    print(f"ROC Curves plot saved to '{ROC_PLOT_PATH}'")

    # Step 5: Log to MLflow
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    with mlflow.start_run(run_name=f"cross_dataset_eval_{time.strftime('%Y%m%d_%H%M%S')}"):
        mlflow.log_param("sample_size_per_variant", sample_size if sample_size else "FULL")
        mlflow.log_param("model_type", "IsolationForest_Tuned")
        mlflow.log_artifact(SUMMARY_CSV_PATH)
        mlflow.log_artifact(ROC_PLOT_PATH)

        for r in results:
            prefix = r["dataset_name"].replace(" ", "_").replace("(", "").replace(")", "").lower()
            mlflow.log_metric(f"{prefix}_auc", r["auc"])
            mlflow.log_metric(f"{prefix}_detection_rate_pct", r["detection_rate_pct"])
            mlflow.log_metric(f"{prefix}_model_psi", r["model_output_psi"])

        print(f"Logged cross-dataset metrics and artifacts to MLflow experiment '{MLFLOW_EXPERIMENT}'")

    # Step 6: Log Provenance
    try:
        engine = get_engine()
        log_provenance(
            engine,
            script_name="cross_dataset_evaluation.py",
            source_dataset="Base.csv, Variant I-V.csv",
            target_table=SUMMARY_CSV_PATH,
            row_count=sum(r["total_rows"] for r in results),
            notes=f"Cross-dataset evaluation completed across 6 BAF datasets. Base AUC: {results[0]['auc']:.4f}, Variant AUC range: {min(r['auc'] for r in results):.4f} - {max(r['auc'] for r in results):.4f}",
        )
        print("Logged run to data_provenance table.")
    except Exception as e:
        print(f"(non-fatal) Provenance logging skipped: {e}")

    print("\n[DONE] Cross-dataset evaluation completed successfully.")
    return 0


if __name__ == "__main__":
    exit(main())
