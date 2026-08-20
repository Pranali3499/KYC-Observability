"""
counterfactual_analysis.py
Stage 4 -- Counterfactual Analysis (Explainability & XAI Layer)
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Complements shap_explainability.py with a different question. SHAP
answers "why was this record flagged" (feature attribution). This
script answers "what would need to change for it NOT to be flagged"
(counterfactual / what-if) -- the actionable half of an analyst-facing
explanation, per Layer 4's "Counterfactual Engine: minimal changes in
features -> anomalous state" box.

METHOD (deliberately simple, appropriate for a PoC dissertation):
For each flagged anomaly, define "normal" as the median of every
6-feature vector the tuned model does NOT flag as anomalous, computed
over the reference (behavioral_features) population. Then:

  1. FULL-VECTOR counterfactual: linearly interpolate the record's
     features toward the normal median (fraction t in [0,1]) and scan
     for the smallest t at which the model's prediction flips from
     anomalous to normal. This answers "how far toward 'typical
     behavior', overall, would this record need to move?"

  2. SINGLE-FEATURE counterfactuals: for each of the 6 features, hold
     the other 5 fixed and scan that feature alone from its current
     value toward the normal median, looking for a flip. This answers
     "could changing just ONE thing have avoided the flag, and which
     one is cheapest to change?" -- often no single feature achieves
     this alone (Isolation Forest anomalies are usually multi-feature
     combinations), which is itself a reportable finding, not a
     failure of the method.

This is a linear SCAN (not gradient-based or a formal optimization,
e.g. DiCE/Wachter-style counterfactuals) -- appropriate given Isolation
Forest has no smooth decision boundary to differentiate through, and
sufficient for a PoC-scope explanation layer.

DELIBERATE REUSE: FEATURE_COLUMNS, TUNED_MODEL_PATH, and load_model()
are imported from kafka_consumer_etl.py rather than redefined here --
same reasoning as api.py's design choice (see that file's docstring).

Usage:
    python counterfactual_analysis.py --n-samples 100

Requires:
    pip install matplotlib (already in requirements.txt)
"""

import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from db_config import get_engine
from provenance import log_provenance
from kafka_consumer_etl import FEATURE_COLUMNS, TUNED_MODEL_PATH, load_model

SOURCE_TABLE = "behavioral_features"
OUTPUT_TABLE = "counterfactual_explanations"
SUMMARY_PLOT_PATH = "counterfactual_summary_plot.png"

SCAN_STEPS = 50  # resolution of the linear scan from t=0 (original) to t=1 (fully normal-median)

CREATE_OUTPUT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {OUTPUT_TABLE} (
    id SERIAL PRIMARY KEY,
    row_id BIGINT,
    fraud_bool INTEGER,
    anomaly_score DOUBLE PRECISION,
    full_vector_flip_fraction DOUBLE PRECISION,   -- NULL if no flip found within scan range
    easiest_feature_1 TEXT,
    easiest_feature_1_shift DOUBLE PRECISION,
    easiest_feature_2 TEXT,
    easiest_feature_2_shift DOUBLE PRECISION,
    easiest_feature_3 TEXT,
    easiest_feature_3_shift DOUBLE PRECISION,
    analyzed_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


def load_data_and_model(engine, n_samples: int):
    print(f"Loading tuned model from '{TUNED_MODEL_PATH}'...")
    model = load_model()

    print(f"Loading '{SOURCE_TABLE}' and identifying flagged anomalies...")
    limit_clause = f"LIMIT {max(n_samples * 50, 25000)}" if (n_samples and n_samples < 50000) else ""
    df = pd.read_sql(f"SELECT * FROM {SOURCE_TABLE} {limit_clause}", engine)

    X_full = df[FEATURE_COLUMNS]
    predictions = model.predict(X_full)
    df["is_anomaly"] = predictions == -1

    normal_rows = df[~df["is_anomaly"]]
    normal_median = normal_rows[FEATURE_COLUMNS].median()
    print(f"Computed 'normal' reference vector from {len(normal_rows):,} non-flagged rows:")
    for col in FEATURE_COLUMNS:
        print(f"  {col}: {normal_median[col]:.4f}")

    anomalies = df[df["is_anomaly"]]
    print(f"Total flagged anomalies in dataset: {len(anomalies):,}")
    if n_samples is None or n_samples in (0, len(anomalies)):
        sample = anomalies
        print(f"Analyzing counterfactuals for ALL {len(anomalies):,} flagged anomalies across dataset")
    else:
        sample_size = min(n_samples, len(anomalies))
        sample = anomalies.sample(n=sample_size, random_state=42)
        print(f"Analyzing counterfactuals for a sample of {sample_size:,} flagged anomalies")

    return model, sample, normal_median


def full_vector_counterfactual(model, original: pd.Series, normal_median: pd.Series) -> float | None:
    """
    Scans t from 0 (original values) to 1 (fully at normal median),
    moving ALL features together, for the smallest t where the
    prediction flips to normal. Returns None if no flip occurs even
    at t=1.0 (rare -- would mean this record's 6-feature vector is
    still flagged even when identical to the population's own median,
    which can happen if OTHER features not in this space, or model
    randomness, drove the original flag).
    """
    for t in np.linspace(0, 1, SCAN_STEPS + 1)[1:]:  # skip t=0, that's the original (already anomalous)
        interpolated = original[FEATURE_COLUMNS] * (1 - t) + normal_median * t
        X = pd.DataFrame([interpolated])[FEATURE_COLUMNS]
        if model.predict(X)[0] == 1:  # 1 == normal, -1 == anomaly
            return float(t)
    return None


def single_feature_counterfactual(model, original: pd.Series, normal_median: pd.Series, feature: str) -> float | None:
    """
    Same idea as full_vector_counterfactual, but only ONE feature moves
    toward its normal-median value; the other 5 stay fixed at their
    original values. Returns the minimal shift (0 to 1) needed, or None
    if changing this feature alone -- however far -- never flips the
    prediction.
    """
    for t in np.linspace(0, 1, SCAN_STEPS + 1)[1:]:
        modified = original[FEATURE_COLUMNS].copy()
        modified[feature] = original[feature] * (1 - t) + normal_median[feature] * t
        X = pd.DataFrame([modified])[FEATURE_COLUMNS]
        if model.predict(X)[0] == 1:
            return float(t)
    return None


def analyze_sample(model, sample: pd.DataFrame, normal_median: pd.Series) -> pd.DataFrame:
    print(f"Running counterfactual scan ({SCAN_STEPS} steps) for each sampled record...")
    rows = []
    per_feature_shifts = {col: [] for col in FEATURE_COLUMNS}  # for the summary plot

    for i, (_, record) in enumerate(sample.iterrows(), start=1):
        full_t = full_vector_counterfactual(model, record, normal_median)

        single_shifts = {}
        for col in FEATURE_COLUMNS:
            shift = single_feature_counterfactual(model, record, normal_median, col)
            single_shifts[col] = shift
            if shift is not None:
                per_feature_shifts[col].append(shift)

        # Rank achievable single-feature flips by smallest required shift
        # ("easiest" = cheapest single change that would have avoided the flag)
        achievable = sorted(
            [(col, s) for col, s in single_shifts.items() if s is not None],
            key=lambda pair: pair[1],
        )
        top3 = achievable[:3]

        row = {
            "row_id": record.get("row_id"),
            "fraud_bool": int(record.get("fraud_bool")) if pd.notna(record.get("fraud_bool")) else None,
            "anomaly_score": float(-model.decision_function(pd.DataFrame([record[FEATURE_COLUMNS]]))[0]),
            "full_vector_flip_fraction": full_t,
        }
        for j in range(3):
            if j < len(top3):
                row[f"easiest_feature_{j+1}"] = top3[j][0]
                row[f"easiest_feature_{j+1}_shift"] = top3[j][1]
            else:
                row[f"easiest_feature_{j+1}"] = None
                row[f"easiest_feature_{j+1}_shift"] = None
        rows.append(row)

        if i % 20 == 0 or i == len(sample):
            print(f"  Analyzed {i}/{len(sample)} records")

    results_df = pd.DataFrame(rows)
    return results_df, per_feature_shifts


def write_results(engine, results_df: pd.DataFrame):
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text(CREATE_OUTPUT_TABLE_SQL))
        conn.commit()
    results_df.to_sql(OUTPUT_TABLE, engine, if_exists="replace", index=False, method="multi", chunksize=500)
    print(f"Wrote {len(results_df)} counterfactual explanations to '{OUTPUT_TABLE}'")


def save_summary_plot(per_feature_shifts: dict):
    """
    Bar chart of the AVERAGE shift required per feature, across records
    where that single feature alone could achieve a flip. A shorter bar
    means that feature is, on average, the "cheapest" single lever to
    move a record out of anomalous territory -- a different,
    complementary lens to SHAP's "biggest contributor to the flag"
    importance chart.
    """
    print("Generating counterfactual summary plot...")
    means = {}
    counts = {}
    for col, shifts in per_feature_shifts.items():
        if shifts:
            means[col] = np.mean(shifts)
            counts[col] = len(shifts)

    if not means:
        print("  No single-feature flips found across the sample -- skipping plot "
              "(this itself is a reportable finding: anomalies here are multi-feature, "
              "not single-feature, in nature).")
        return

    features = list(means.keys())
    values = [means[f] for f in features]
    labels = [f"{f}\n(n={counts[f]})" for f in features]

    plt.figure(figsize=(8, 5))
    plt.barh(labels, values, color="#4C72B0")
    plt.xlabel("Average shift required to flip to 'normal' (0=none, 1=full move to population median)")
    plt.title("Counterfactual Analysis: Average Cost to Un-Flag a Record\n(lower = cheaper single-feature lever)")
    plt.tight_layout()
    plt.savefig(SUMMARY_PLOT_PATH, dpi=150)
    plt.close()
    print(f"Saved to '{SUMMARY_PLOT_PATH}'")


def print_example_counterfactuals(results_df: pd.DataFrame, n: int = 5):
    print("\n" + "=" * 65)
    print(f"EXAMPLE COUNTERFACTUALS (first {n} of {len(results_df)} sampled records)")
    print("=" * 65)
    for _, row in results_df.head(n).iterrows():
        fraud_label = "ACTUAL FRAUD" if row["fraud_bool"] == 1 else "not fraud (per label)"
        print(f"\nrow_id={row['row_id']}  anomaly_score={row['anomaly_score']:.4f}  [{fraud_label}]")
        if row["full_vector_flip_fraction"] is not None:
            print(f"  Full-vector: moving {row['full_vector_flip_fraction']:.0%} of the way toward "
                  f"typical behavior (all 6 features together) would un-flag this record.")
        else:
            print(f"  Full-vector: no flip found even at 100% shift toward the population median.")
        if row["easiest_feature_1"] is not None:
            print(f"  Easiest single change: {row['easiest_feature_1']} "
                  f"(shift {row['easiest_feature_1_shift']:.0%} toward typical alone would flip it)")
        else:
            print(f"  No single feature, changed alone, flips this record -- it's a multi-feature anomaly.")


def summarize_single_feature_achievability(results_df: pd.DataFrame):
    """
    Reports both HOW OFTEN a single-feature flip is achievable, and --
    the number that actually matters for the combinatorial-vs-simple
    question -- HOW BIG a shift that easiest single-feature change
    typically requires. Achievability alone is a weak signal here: by
    construction, scanning a feature all the way to t=1.0 (the full
    population median) makes a flip achievable for almost any record
    given enough shift, so a high achievability rate does NOT by
    itself indicate "simple, single-variable anomalies." The shift
    MAGNITUDE is what distinguishes "cheap, one-variable outlier"
    (small shift) from "technically achievable but requires nearly the
    full move to typical" (large shift, effectively still
    multi-feature/combinatorial in character).
    """
    n_total = len(results_df)
    n_single_achievable = results_df["easiest_feature_1"].notna().sum()
    n_full_achievable = results_df["full_vector_flip_fraction"].notna().sum()

    shifts = results_df["easiest_feature_1_shift"].dropna()
    mean_shift = shifts.mean() if len(shifts) else None
    median_shift = shifts.median() if len(shifts) else None

    print("\n" + "=" * 65)
    print("ACHIEVABILITY SUMMARY")
    print("=" * 65)
    print(f"Full-vector counterfactual found:     {n_full_achievable}/{n_total} "
          f"({n_full_achievable/n_total:.1%})")
    print(f"Single-feature counterfactual found:  {n_single_achievable}/{n_total} "
          f"({n_single_achievable/n_total:.1%})")

    if mean_shift is not None:
        print(f"Mean shift required for the easiest single-feature flip:   {mean_shift:.1%}")
        print(f"Median shift required for the easiest single-feature flip: {median_shift:.1%}")
        if median_shift >= 0.5:
            print("High median shift: even where a single feature CAN flip a record, it typically "
                  "requires moving that feature most of the way to the population median. This "
                  "supports a combinatorial-fraud premise -- no single feature is a cheap, "
                  "isolated red flag on its own; achievability is technically high but the "
                  "'cost' of a single-feature explanation is also high.")
        else:
            print("Low median shift: for many flagged records, a SMALL change in a single feature "
                  "would have been enough to avoid the flag. This nuances a purely combinatorial "
                  "narrative -- a meaningful share of anomalies here behave closer to single-variable "
                  "outliers than genuine multi-feature combinations. Worth discussing honestly in the "
                  "report rather than assumed away: it suggests some flagged records may be driven "
                  "by one dominant feature, with the others contributing comparatively little.")
    else:
        print("No single-feature flips were found across the sample -- every flagged anomaly "
              "required a full multi-feature shift, a strong combinatorial signal.")


def main():
    parser = argparse.ArgumentParser(description="Stage 4: Counterfactual analysis")
    parser.add_argument("--n-samples", type=int, default=None,
                         help="Number of flagged anomalies to analyze (default: None for all flagged anomalies)")
    args = parser.parse_args()

    print("=" * 65)
    print("STAGE 4 -- Counterfactual Analysis")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    engine = get_engine()
    model, sample, normal_median = load_data_and_model(engine, args.n_samples)
    results_df, per_feature_shifts = analyze_sample(model, sample, normal_median)

    write_results(engine, results_df)
    save_summary_plot(per_feature_shifts)
    print_example_counterfactuals(results_df)
    summarize_single_feature_achievability(results_df)

    log_provenance(
        engine,
        script_name="counterfactual_analysis.py",
        source_dataset=SOURCE_TABLE,
        target_table=OUTPUT_TABLE,
        row_count=len(results_df),
        notes=f"Linear-scan counterfactuals, {len(results_df)} flagged anomalies analyzed",
    )

    print("\n[DONE] Counterfactual analysis complete. "
          f"See '{OUTPUT_TABLE}' table and '{SUMMARY_PLOT_PATH}' for report figures.")


if __name__ == "__main__":
    main()
