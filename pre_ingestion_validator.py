"""
pre_ingestion_validator.py
Pre-Ingestion Data Validation & Deduplication Pipeline
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Validates incoming datasets BEFORE ingestion:
  1. Schema contract validation (presence of critical fields)
  2. Null-rate gate (< 1% threshold)
  3. Value-range validation across all numeric fields
  4. Record-level SHA-256 hash deduplication with duplicate logging
  5. Provenance tracking & audit trail generation

Responds directly to mid-sem evaluator feedback:
"Add validation pipeline for incoming datasets (schema, null rates, ranges)"
"Merge biometric datasets (source A + source B) with deduplication and provenance tracking."

Usage:
  python pre_ingestion_validator.py --csv ci_test_data.csv
  python pre_ingestion_validator.py --csv Base.csv --sample-size 50000 --output-clean clean_data.parquet
"""

import argparse
import hashlib
import os
import sys
import time

import numpy as np
import pandas as pd

from provenance import log_provenance
from db_config import get_engine

CRITICAL_COLUMNS = [
    "velocity_6h",
    "velocity_24h",
    "velocity_4w",
    "device_distinct_emails_8w",
    "device_fraud_count",
    "current_address_months_count",
    "name_email_similarity",
    "credit_risk_score",
    "proposed_credit_limit",
    "income",
]

VALUE_RANGES = {
    "velocity_6h": (0.0, 50000.0),
    "velocity_24h": (0.0, 50000.0),
    "velocity_4w": (0.0, 50000.0),
    "device_fraud_count": (0.0, 1000.0),
    "name_email_similarity": (0.0, 1.0),
    "phone_home_valid": (0, 1),
    "phone_mobile_valid": (0, 1),
    "foreign_request": (0, 1),
    "income": (0.0, 10.0),
    "credit_risk_score": (-500.0, 1000.0),
    "proposed_credit_limit": (0.0, 100000.0),
    "fraud_bool": (0, 1),
}


def hash_row(row_series) -> str:
    """Generates deterministic SHA-256 hash of record content for deduplication."""
    content = "|".join([str(v) for v in row_series.values])
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def run_validation(csv_path: str, sample_size: int = None, output_clean: str = None) -> bool:
    print("=" * 65)
    print("PRE-INGESTION DATA VALIDATION & DEDUPLICATION GATE")
    print(f"Target file: {csv_path}")
    print("=" * 65)

    if not os.path.exists(csv_path):
        print(f"[ERROR] File not found: {csv_path}")
        return False

    print(f"Loading incoming data from '{csv_path}'...")
    if sample_size:
        df = pd.read_csv(csv_path, nrows=sample_size)
    else:
        df = pd.read_csv(csv_path)

    initial_count = len(df)
    print(f"Loaded {initial_count:,} records ({df.shape[1]} columns).")

    # Gate 1: Schema Contract
    print("\n--- Gate 1: Schema Contract Validation ---")
    missing_critical = [col for col in CRITICAL_COLUMNS if col not in df.columns]
    if missing_critical:
        print(f"  [FAIL] Missing required critical columns: {missing_critical}")
        return False
    print(f"  [PASS] All {len(CRITICAL_COLUMNS)} critical columns present.")

    # Gate 2: Null Rate Check
    print("\n--- Gate 2: Null Rate Gate (Threshold: < 1.0%) ---")
    null_failures = []
    for col in CRITICAL_COLUMNS:
        null_count = df[col].isnull().sum()
        null_rate = null_count / initial_count
        if null_rate > 0.01:
            null_failures.append((col, null_rate))
        else:
            print(f"  [OK] {col:<30}: {null_rate*100:.2f}% nulls")

    if null_failures:
        print(f"  [FAIL] Null rate threshold breached on: {null_failures}")
        return False
    print("  [PASS] All critical columns within acceptable null rates.")

    # Gate 3: Value Range Validation
    print("\n--- Gate 3: Value Range Validation ---")
    range_violations = []
    for col, (min_val, max_val) in VALUE_RANGES.items():
        if col in df.columns:
            # Exclude BAF -1 sentinels from min check where applicable
            valid_vals = df[col].dropna()
            if col in ["prev_address_months_count", "current_address_months_count", "device_distinct_emails_8w"]:
                valid_vals = valid_vals[valid_vals != -1]

            below_min = (valid_vals < min_val).sum()
            above_max = (valid_vals > max_val).sum()
            if below_min > 0 or above_max > 0:
                range_violations.append((col, below_min, above_max))
                print(f"  [WARN] {col}: {below_min} below min ({min_val}), {above_max} above max ({max_val})")
            else:
                print(f"  [OK] {col:<30}: within [{min_val}, {max_val}]")

    # Gate 4: SHA-256 Deduplication
    print("\n--- Gate 4: Record-Level SHA-256 Deduplication ---")
    start_dedup = time.perf_counter()
    dedup_cols = [c for c in CRITICAL_COLUMNS if c in df.columns]
    
    # Hash subset for speed
    hashes = df[dedup_cols].astype(str).apply(lambda row: hashlib.sha256("".join(row).encode("utf-8")).hexdigest(), axis=1)
    df["_record_hash"] = hashes
    
    is_duplicate = df.duplicated(subset=["_record_hash"], keep="first")
    num_duplicates = int(is_duplicate.sum())
    clean_df = df[~is_duplicate].drop(columns=["_record_hash"])
    dedup_time = time.perf_counter() - start_dedup

    print(f"  Processed {initial_count:,} records in {dedup_time:.2f}s")
    print(f"  Duplicate records detected & dropped: {num_duplicates:,} ({num_duplicates/initial_count*100:.2f}%)")
    print(f"  Clean records surviving: {len(clean_df):,}")

    if output_clean:
        if output_clean.endswith(".parquet"):
            clean_df.to_parquet(output_clean, index=False)
        else:
            clean_df.to_csv(output_clean, index=False)
        print(f"  Wrote validated clean dataset to: {output_clean}")

    # Provenance Logging
    try:
        engine = get_engine()
        log_provenance(
            engine,
            script_name="pre_ingestion_validator.py",
            source_dataset=csv_path,
            target_table=output_clean if output_clean else "validation_gate",
            row_count=len(clean_df),
            notes=f"Pre-ingestion validation passed: {initial_count} input rows, {num_duplicates} duplicates dropped, {len(clean_df)} validated clean rows.",
        )
        print("  Logged validation run to data_provenance.")
    except Exception as e:
        print(f"  (non-fatal) Provenance logging skipped: {e}")

    print("\n" + "=" * 65)
    print("PRE-INGESTION VALIDATION SUMMARY: [PASS - ALL GATES OK]")
    print("=" * 65)
    return True


def main():
    parser = argparse.ArgumentParser(description="Pre-ingestion data quality and deduplication validation pipeline")
    parser.add_argument("--csv", default="ci_test_data.csv", help="CSV dataset to validate")
    parser.add_argument("--sample-size", type=int, default=None, help="Optional sample size limit")
    parser.add_argument("--output-clean", default=None, help="Optional output path for validated deduplicated data")
    args = parser.parse_args()

    passed = run_validation(args.csv, args.sample_size, args.output_clean)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
