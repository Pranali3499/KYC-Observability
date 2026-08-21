"""
drift_detection.py
Stage 6 -- Monitoring & Drift Detection (PSI / KS)

Compares the "live" scoring population against the reference
(training-time) distribution for each behavioral feature, using:

    PSI  (Population Stability Index) -- bucketed distribution shift
    KS   (Kolmogorov-Smirnov 2-sample test) -- distribution-shape shift

Writes a per-feature drift report to PostgreSQL (drift_report table)
and prints a PASS/WARNING/ALERT summary in the same style as your
other demo pieces, so it can slot into midsem_demo.py-style output.

REFERENCE population : behavioral_features (1M training rows), scored
                        HERE using the same isolation_forest_tuned.pkl
                        that kafka_consumer_etl.py uses -- NOT read from
                        the anomaly_scores table, which holds scores
                        from a DIFFERENT, untuned model trained on a
                        different feature set (see anomaly_detection.py:
                        it fits a fresh IsolationForest(n_estimators=100,
                        contamination=0.011) on 7 features including the
                        composite risk_anomaly_score every run). Comparing
                        that model's scores against the tuned model's
                        live scores would be comparing two different
                        scoring functions, not detecting drift.
LIVE population       : real_time_scores -- kafka_consumer_etl.py
                        output (6 raw features + anomaly_score, scored
                        by isolation_forest_tuned.pkl)

Checks two things:
  1. Per-feature drift on the 6 behavioral features shared by both
     pipelines.
  2. Model OUTPUT score drift -- both sides now scored by the SAME
     tuned model on the SAME 6 features, so this is a genuine
     apples-to-apples comparison.

If LIVE_TABLE does not exist yet (e.g. your Kafka consumer isn't
populating it continuously), this script falls back to a SYNTHETIC
drifted sample built from the reference data itself, so you can
validate and demo the drift logic before real streaming data exists.
This fallback is clearly logged -- swap LIVE_TABLE the moment your
consumer table is live and this code path won't trigger.

--- CHANGE LOG (added to fix a real bug found via integration testing) ---
Replaced the Unicode flag characters (checkmark/warning-triangle/cross,
U+2713 / U+26A0 / U+2717) with plain ASCII equivalents ([OK]/[!]/[X]).
Those characters printed fine in an interactive Windows terminal (which
uses a Unicode-capable code page), but crashed with a UnicodeEncodeError
under cp1252 whenever this script's output was captured non-interactively
-- e.g. by a subprocess pipe, a CI runner, or another script piping this
one's stdout. Confirmed via a real integration test
(tests/test_integration_pipeline.py) that ran this script as a
subprocess and hit exactly this crash, even though the underlying
PSI/KS computation had already completed successfully. No logic,
thresholds, or database writes were changed -- only the two print
statements that used these characters.
----------------------------------------------------------------------

Requires:
    pip install scipy joblib

Usage:
    python drift_detection.py
"""

import joblib
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp
from sqlalchemy import text, inspect
from db_config import get_engine
from provenance import log_provenance
from alerting import send_drift_alert

TUNED_MODEL_PATH = "isolation_forest_tuned.pkl"  # same model kafka_consumer_etl.py uses

REFERENCE_TABLE = "behavioral_features"
LIVE_TABLE = "real_time_scores"             # kafka_consumer_etl.py output
TARGET_TABLE = "drift_report"

# Same 6 features the tuned model was trained on -- matches
# kafka_consumer_etl.py's FEATURE_COLUMNS and shap_explainability.py's
# FEATURE_COLUMNS exactly. NOT the same 7-feature list anomaly_detection.py
# uses for its own separate (untuned) baseline model.
FEATURE_COLS = [
    "session_velocity_score",
    "device_reuse_score",
    "address_stability_score",
    "identity_consistency_score",
    "geographic_risk_score",
    "financial_risk_score",
]

SCORE_COL_REFERENCE = "anomaly_score_tuned_batch"  # computed here, on reference data
SCORE_COL_LIVE = "anomaly_score"                   # already in real_time_scores
SCORE_FEATURE_NAME = "model_anomaly_score"         # label used in the drift report

N_BINS = 10
PSI_WARN = 0.10     # 0.10-0.25 -> moderate shift, watch it
PSI_ALERT = 0.25    # >0.25 -> significant shift, investigate/retrain
KS_ALPHA = 0.05      # p-value below this -> distributions differ significantly

# ASCII-safe status flags -- see CHANGE LOG above. Plain text, not
# Unicode symbols, so this script's output can safely be captured by
# a subprocess pipe / CI runner / another script on any platform or
# console code page.
FLAG_OK = "[OK]"
FLAG_WARNING = "[!]"
FLAG_ALERT = "[X]"


import argparse
import os


def load_reference(engine, sample_size: int = None) -> pd.DataFrame:
    if sample_size and sample_size > 0:
        print(f"Reading reference distribution from '{REFERENCE_TABLE}' (sample_size={sample_size:,}) ...")
        df = pd.read_sql(f"SELECT * FROM {REFERENCE_TABLE} LIMIT {sample_size}", engine)
    else:
        print(f"Reading reference distribution from '{REFERENCE_TABLE}' ...")
        df = pd.read_sql(f"SELECT * FROM {REFERENCE_TABLE}", engine)
    print(f"Loaded {len(df):,} reference rows")
    return df


def score_reference_with_tuned_model(reference: pd.DataFrame) -> pd.DataFrame:
    """
    Scores the reference data with the SAME tuned model the streaming
    path uses, so model-score drift is a genuine apples-to-apples
    comparison instead of comparing two different models.
    """
    print(f"Loading tuned model from '{TUNED_MODEL_PATH}' to score reference data...")
    model = joblib.load(TUNED_MODEL_PATH)

    X = reference[FEATURE_COLS]
    reference = reference.copy()
    reference[SCORE_COL_REFERENCE] = -model.decision_function(X)
    print(f"  Scored {len(reference):,} reference rows with the tuned model")
    return reference


def table_exists(engine, table_name: str) -> bool:
    return inspect(engine).has_table(table_name)


def load_live(engine, reference: pd.DataFrame, sample_size: int = None) -> tuple[pd.DataFrame, bool]:
    """
    Returns (live_df, is_synthetic). is_synthetic=True means LIVE_TABLE
    wasn't found and a synthetic drifted sample was generated instead.
    """
    if table_exists(engine, LIVE_TABLE):
        print(f"Reading live distribution from '{LIVE_TABLE}' ...")
        limit_sql = f"LIMIT {sample_size}" if sample_size else ""
        df = pd.read_sql(f"SELECT * FROM {LIVE_TABLE} {limit_sql}", engine)
        print(f"Loaded {len(df):,} live rows")
        return df, False

    print(f"'{LIVE_TABLE}' not found -- generating SYNTHETIC drifted sample for demo purposes.")
    print("  (replace with your Kafka consumer's live table once it's populated)")
    sample = reference.sample(n=min(20_000, len(reference)), random_state=7).copy()

    # Inject a plausible drift pattern: device reuse and session velocity
    # trending up (e.g. mimics a coordinated bot-onboarding attack),
    # address stability trending down.
    rng = np.random.default_rng(42)
    sample["session_velocity_score"] = np.clip(
        sample["session_velocity_score"] + rng.normal(0.15, 0.05, len(sample)), 0, 1
    )
    sample["device_reuse_score"] = np.clip(
        sample["device_reuse_score"] + rng.normal(0.20, 0.05, len(sample)), 0, 1
    )
    sample["address_stability_score"] = np.clip(
        sample["address_stability_score"] - rng.normal(0.10, 0.05, len(sample)), 0, 1
    )

    # Mirror the model-score column name real_time_scores actually uses,
    # with the same directional drift baked in, so score-drift can also
    # be demoed even without a live real_time_scores table yet.
    if SCORE_COL_REFERENCE in sample.columns:
        sample[SCORE_COL_LIVE] = sample[SCORE_COL_REFERENCE] + rng.normal(0.05, 0.02, len(sample))

    return sample, True


def compute_psi(reference: pd.Series, live: pd.Series, bins: int = N_BINS) -> float | None:
    """
    Standard bucketed PSI. Bin edges are taken from the reference
    distribution's quantiles so each reference bucket has ~equal mass;
    the live sample is then binned against those same edges.

    Returns None (rather than a misleading number) if the live sample
    is empty after dropping NaNs -- this replaces a prior bug where an
    empty live bin count could divide-by-zero into NaN.
    """
    ref = reference.astype(float).dropna()
    liv = live.astype(float).dropna()

    if len(liv) == 0:
        return None

    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0  # feature has ~no variance in reference; skip

    ref_counts, _ = np.histogram(ref, bins=edges)
    liv_counts, _ = np.histogram(liv, bins=edges)

    if liv_counts.sum() == 0:
        return None

    ref_pct = np.clip(ref_counts / ref_counts.sum(), 1e-6, None)
    liv_pct = np.clip(liv_counts / liv_counts.sum(), 1e-6, None)

    psi = np.sum((liv_pct - ref_pct) * np.log(liv_pct / ref_pct))
    return float(psi)


def classify_psi(psi: float) -> str:
    if psi >= PSI_ALERT:
        return "ALERT"
    if psi >= PSI_WARN:
        return "WARNING"
    return "OK"


MIN_RELIABLE_SAMPLE = 300  # PSI/KS get noisy below this; flag rather than hide it


def run_drift_report(reference: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    print("Computing PSI + KS per feature...")

    if len(live) < MIN_RELIABLE_SAMPLE:
        print(f"  {FLAG_WARNING} WARNING: live sample is only {len(live):,} rows "
              f"(recommended minimum ~{MIN_RELIABLE_SAMPLE:,} for stable PSI/KS). "
              f"Treat any ALERT/WARNING below as a signal to investigate, not a confirmed finding, "
              f"until more live data accumulates.")

    rows = []
    for col in FEATURE_COLS:
        if col not in reference.columns or col not in live.columns:
            print(f"  (skipping {col} -- not present in both tables)")
            continue

        psi = compute_psi(reference[col], live[col])
        if psi is None:
            print(f"  (skipping {col} -- live sample has no valid values)")
            continue

        ks_stat, ks_p = ks_2samp(reference[col].dropna(), live[col].dropna())
        status = classify_psi(psi)
        if ks_p < KS_ALPHA:
            status = "ALERT" if status == "OK" else status  # KS can escalate an OK to a flag

        rows.append({
            "feature": col,
            "psi": round(psi, 4),
            "ks_statistic": round(float(ks_stat), 4),
            "ks_pvalue": round(float(ks_p), 6),
            "status": status,
            "sample_size_warning": len(live) < MIN_RELIABLE_SAMPLE,
        })
        flag = FLAG_OK if status == "OK" else (FLAG_WARNING if status == "WARNING" else FLAG_ALERT)
        print(f"  {flag} {col:<28} PSI={psi:.4f}  KS_stat={ks_stat:.4f}  KS_p={ks_p:.6f}  [{status}]")

    # Model output score drift -- different column names in each table
    # (see SCORE_COL_REFERENCE / SCORE_COL_LIVE), same underlying quantity.
    if SCORE_COL_REFERENCE in reference.columns and SCORE_COL_LIVE in live.columns:
        psi = compute_psi(reference[SCORE_COL_REFERENCE], live[SCORE_COL_LIVE])
        if psi is None:
            print(f"  (skipping {SCORE_FEATURE_NAME} -- live sample has no valid values)")
        else:
            ks_stat, ks_p = ks_2samp(
                reference[SCORE_COL_REFERENCE].dropna(), live[SCORE_COL_LIVE].dropna()
            )
            status = classify_psi(psi)
            if ks_p < KS_ALPHA:
                status = "ALERT" if status == "OK" else status

            rows.append({
                "feature": SCORE_FEATURE_NAME,
                "psi": round(psi, 4),
                "ks_statistic": round(float(ks_stat), 4),
                "ks_pvalue": round(float(ks_p), 6),
                "status": status,
                "sample_size_warning": len(live) < MIN_RELIABLE_SAMPLE,
            })
            flag = FLAG_OK if status == "OK" else (FLAG_WARNING if status == "WARNING" else FLAG_ALERT)
            print(f"  {flag} {SCORE_FEATURE_NAME:<28} PSI={psi:.4f}  KS_stat={ks_stat:.4f}  KS_p={ks_p:.6f}  [{status}]")
    else:
        print(f"  (skipping {SCORE_FEATURE_NAME} -- score column missing from reference or live)")

    return pd.DataFrame(rows)


def write_report(report: pd.DataFrame, engine, is_synthetic: bool):
    report = report.copy()
    report["run_timestamp"] = pd.Timestamp.utcnow()
    report["live_source"] = "synthetic_demo_sample" if is_synthetic else LIVE_TABLE

    print(f"Writing drift report to '{TARGET_TABLE}' ...")
    report.to_sql(TARGET_TABLE, engine, if_exists="append", index=False, method="multi")


def main():
    parser = argparse.ArgumentParser(description="Stage 6: Drift Detection (PSI / KS)")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=int(os.getenv("DRIFT_SAMPLE_SIZE", "50000")),
        help="Number of reference rows to load and score (default: 50000, 0 for all)",
    )
    args = parser.parse_args()

    print("=" * 65)
    print("STAGE 6 -- Drift Detection (PSI / KS)")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    engine = get_engine()
    sample_size = args.sample_size if args.sample_size > 0 else None
    reference = load_reference(engine, sample_size=sample_size)
    reference = score_reference_with_tuned_model(reference)
    live, is_synthetic = load_live(engine, reference, sample_size=sample_size)

    report = run_drift_report(reference, live)
    write_report(report, engine, is_synthetic)

    n_alert = (report["status"] == "ALERT").sum()
    n_warn = (report["status"] == "WARNING").sum()

    print("-" * 65)
    sample_caveat = " (LOW CONFIDENCE -- live sample below reliable minimum, see warning above)" \
        if len(live) < MIN_RELIABLE_SAMPLE else ""
    if n_alert:
        print(f"[drift] ALERT -- {n_alert} feature(s) show significant drift.{sample_caveat} Retraining/canary review recommended.")
    elif n_warn:
        print(f"[drift] WARNING -- {n_warn} feature(s) show moderate drift.{sample_caveat} Monitor closely.")
    else:
        print(f"[drift] PASS -- No significant drift detected across monitored features.{sample_caveat}")

    send_drift_alert(report, len(live), is_synthetic)

    log_provenance(
        engine,
        script_name="drift_detection.py",
        source_dataset=f"{REFERENCE_TABLE} vs {'synthetic_demo_sample' if is_synthetic else LIVE_TABLE}",
        target_table=TARGET_TABLE,
        row_count=len(report),
    )


if __name__ == "__main__":
    main()
