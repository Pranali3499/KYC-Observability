"""
ci/generate_synthetic_baf.py
Generates a small, synthetic dataset matching the real BAF schema
closely enough to exercise data_ingestion.py -> feature_engineering.py
-> data_quality_checks.py end-to-end inside an isolated CI Postgres
container.

WHY THIS EXISTS:
Your real data_ingestion.py/feature_engineering.py write with
if_exists="replace" directly to kyc_transactions/behavioral_features
-- your real, live, 1,000,000-row tables. Running them in CI against
YOUR real database would be dangerous (see test_regression_baseline.py's
docstring for the full explanation of that risk). GitHub Actions gives
each workflow run a completely fresh, isolated, throwaway Postgres
service container -- nothing this script writes ever touches your real
data, because it's a different database entirely, torn down when the
CI run ends.

This generates a SMALL (default 500 rows) synthetic file with:
  - The 18 real column names feature_engineering.py's COLS mapping and
    SENTINEL_COLS actually reference (confirmed from the real source),
    populated with plausible values including some -1 sentinels (BAF's
    real missing-data encoding, which clean_sentinels() is specifically
    designed to handle).
  - 14 additional filler columns, so the total column count reaches 32
    -- matching data_quality_checks.py's schema_check() expectation of
    kyc_transactions having >= 32 columns. Filler column CONTENT is not
    meaningful (CI only exercises schema/pipeline correctness here, not
    a second copy of your real behavioral analysis) -- filler columns
    exist purely to satisfy the schema check honestly, not to fake
    additional real signal.

Usage:
    python ci/generate_synthetic_baf.py --n-rows 500 --output ci_test_data.csv
"""

import argparse

import numpy as np
import pandas as pd


def generate(n_rows: int, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    df = pd.DataFrame({
        # --- The 18 real columns feature_engineering.py actually reads ---
        "velocity_6h": rng.uniform(1000, 12000, n_rows),
        "velocity_24h": rng.uniform(1000, 12000, n_rows),
        "velocity_4w": rng.uniform(1000, 12000, n_rows),
        "device_distinct_emails_8w": rng.integers(0, 5, n_rows),
        "device_fraud_count": rng.integers(0, 2, n_rows),
        # Include real -1 sentinels (BAF's actual missing-data encoding)
        # so clean_sentinels() has something real to do, same as it
        # does against the real dataset.
        "prev_address_months_count": rng.choice(
            [-1] + list(range(0, 400)), size=n_rows, p=[0.7] + [0.3 / 400] * 400
        ),
        "current_address_months_count": rng.integers(0, 400, n_rows),
        "name_email_similarity": rng.uniform(0, 1, n_rows),
        "phone_home_valid": rng.integers(0, 2, n_rows),
        "phone_mobile_valid": rng.integers(0, 2, n_rows),
        "foreign_request": rng.integers(0, 2, n_rows),
        "source": rng.choice(["INTERNET", "TELEAPP"], n_rows, p=[0.85, 0.15]),
        "income": rng.uniform(5000, 200000, n_rows),
        "credit_risk_score": rng.uniform(0, 500, n_rows),
        "proposed_credit_limit": rng.uniform(500, 50000, n_rows),
        "bank_months_count": rng.choice(
            [-1] + list(range(0, 300)), size=n_rows, p=[0.25] + [0.75 / 300] * 300
        ),
        "session_length_in_minutes": rng.choice(
            [-1] + list(range(1, 60)), size=n_rows, p=[0.05] + [0.95 / 59] * 59
        ),
        # ~1.1% fraud prevalence, matching the real BAF dataset's
        # documented imbalance, so downstream logic that assumes a
        # rare-event ratio behaves realistically even at small scale.
        "fraud_bool": rng.choice([0, 1], n_rows, p=[0.989, 0.011]),
    })

    # --- Filler columns, purely to reach the real dataset's 32-column
    # count for an honest schema-check pass -- not meant to carry
    # meaningful signal. ---
    for i in range(1, 15):
        df[f"filler_col_{i}"] = rng.uniform(0, 1, n_rows)

    assert df.shape[1] == 32, f"Expected 32 columns to match real BAF schema, got {df.shape[1]}"
    return df


def main():
    parser = argparse.ArgumentParser(description="Generate a small synthetic BAF-schema dataset for CI use.")
    parser.add_argument("--n-rows", type=int, default=500)
    parser.add_argument("--output", default="ci_test_data.csv")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    df = generate(args.n_rows, args.seed)
    df.to_csv(args.output, index=False)
    print(f"Generated {len(df):,} rows x {df.shape[1]} columns -> {args.output}")
    print(f"fraud_bool prevalence: {df['fraud_bool'].mean():.4%}")
    print(f"prev_address_months_count sentinels (-1): {(df['prev_address_months_count'] == -1).sum()}")


if __name__ == "__main__":
    main()
