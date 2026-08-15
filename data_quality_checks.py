"""
data_quality_checks.py
Stage 1 -- Data Quality & Provenance
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Runs schema, null-rate, and value-range checks against kyc_transactions
and behavioral_features, and writes every result to a new
data_quality_report table in PostgreSQL for auditability. This becomes
the CI/CD data-quality gate referenced later in the project plan.

Usage:
    python data_quality_checks.py
    python data_quality_checks.py --db-url postgresql://user:pass@localhost:5432/kyc_db

Exit code is non-zero if any check fails -- this is what a future
CI/CD pipeline (GitHub Actions) can key off to block a bad build.
"""

import argparse
import datetime as dt
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

from db_config import DB_URL as _DEFAULT_DB_URL

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Falls back to the centralized credentials in db_config.py (kyc_user/kyc_db)
# instead of a hardcoded postgres/postgres default -- keeps this script in
# sync with the rest of the pipeline, which already reads db_config.get_engine().
DEFAULT_DB_URL = os.environ.get("KYC_DB_URL", _DEFAULT_DB_URL)

# Null-rate threshold: behavioral_features should be FULLY imputed by the
# feature-engineering step (Demo Piece 2 already handles -1 sentinels), so
# any nulls surviving into this table are a real data-quality problem.
NULL_RATE_THRESHOLD = 0.01  # 1%

# Known valid ranges for engineered/behavioral columns. Extend this as you
# add features -- anything not listed here only gets a null-rate check.
VALUE_RANGES = {
    "session_velocity_score": (0.0, 1.0),
    "device_reuse_score": (0.0, 1.0),
    "address_stability_score": (0.0, 1.0),
    "identity_consistency_score": (0.0, 1.0),
    "geographic_risk_score": (0.0, 1.0),
    "financial_risk_score": (0.0, 1.0),
    "risk_anomaly_score": (0.0, 1.0),          # composite behavioral risk score
    "liveness_score": (0.0, 1.0),
    "face_match_score": (0.0, 1.0),
    "ocr_confidence_score": (0.0, 1.0),
    "biometric_risk_score": (0.0, 1.0),
    "risk_anomaly_score_experimental_with_biometric": (0.0, 1.0),
    "fraud_bool": (0, 1),
}

# --- Biometric result tables (added to close mid-sem evaluator feedback:
# "Add validation pipeline for incoming datasets (schema, null rates,
# ranges)" -- previously this only covered kyc_transactions/
# behavioral_features, not the 4 biometric sub-component result tables.
#
# Column names below are the REAL, confirmed ones -- read directly from
# a live query of each table (via biometric_etl_normalize.py's schema
# inspection), not assumed. document_ocr_results and
# identity_mismatch_results are written by the original project
# scripts; face_match_results and liveness_results are written by the
# patched biometric_face_matching.py / biometric_liveness_detection.py
# that added per-record persistence.
#
# These tables are treated as OPTIONAL, unlike kyc_transactions/
# behavioral_features -- not every checkout will have run every
# biometric script, and a missing table here is a "not yet run" state,
# not necessarily a data quality failure the way a missing core table
# would be.
BIOMETRIC_TABLES = {
    "document_ocr_results": {
        "expected_min_columns": 8,
        "value_ranges": {
            "name_similarity": (0.0, 1.0),
            "ocr_mean_confidence": (0.0, 100.0),  # confirmed on a 0-100 scale, not 0-1
            # id_number_exact_match intentionally excluded from range
            # checking: confirmed live to be stored as a real PostgreSQL
            # BOOLEAN column, not an integer 0/1 as its printed summary
            # ("100.0%") might suggest. A numeric < / > comparison against
            # a boolean column fails at the SQL level (operator does not
            # exist: boolean < integer). A boolean is never "out of
            # range" in the first place -- it's still covered by the
            # null-rate check below, which is the check that actually
            # applies to it.
        },
    },
    "identity_mismatch_results": {
        "expected_min_columns": 9,
        "value_ranges": {
            "face_match_score": (0.0, 1.0),
            "name_similarity": (0.0, 1.0),
            # document_mismatch, biometric_mismatch, identity_mismatch
            # intentionally excluded from range checking -- same reason
            # as id_number_exact_match above: these are almost certainly
            # stored as PostgreSQL BOOLEAN by this project's original
            # script, not integer 0/1. Still covered by the null-rate
            # check below.
        },
    },
    "face_match_results": {
        "expected_min_columns": 4,
        "value_ranges": {
            "true_label": (0, 1),
            "predicted_match_score": (0.0, 1.0),
            "predicted_match": (0, 1),
        },
    },
    "liveness_results": {
        "expected_min_columns": 4,
        "value_ranges": {
            "true_label": (0, 1),
            "predicted_liveness_score": (0.0, 1.0),
            "predicted_real": (0, 1),
        },
    },
}

REPORT_TABLE = "data_quality_report"

CREATE_REPORT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {REPORT_TABLE} (
    id SERIAL PRIMARY KEY,
    run_timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    table_name TEXT NOT NULL,
    check_type TEXT NOT NULL,      -- schema | null_rate | value_range
    column_name TEXT,
    status TEXT NOT NULL,          -- PASS | FAIL | SKIP
    details TEXT
);
"""


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def get_columns(engine, table_name: str) -> pd.DataFrame:
    """Fetch column name + dtype for a table from information_schema."""
    query = text(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = :table_name
        ORDER BY ordinal_position
        """
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"table_name": table_name})


def schema_check(engine, table_name: str, expected_min_columns: int) -> list[dict]:
    """
    Confirms the table exists and has at least the expected number of
    columns. Records the full column list as a provenance snapshot --
    useful later for detecting upstream schema drift if the source CSV
    or ingestion pipeline changes.
    """
    results = []
    cols = get_columns(engine, table_name)

    if cols.empty:
        results.append(
            {
                "table_name": table_name,
                "check_type": "schema",
                "column_name": None,
                "status": "FAIL",
                "details": f"Table '{table_name}' not found or has no columns.",
            }
        )
        return results

    actual_count = len(cols)
    status = "PASS" if actual_count >= expected_min_columns else "FAIL"
    col_list = ", ".join(cols["column_name"].tolist())
    results.append(
        {
            "table_name": table_name,
            "check_type": "schema",
            "column_name": None,
            "status": status,
            "details": (
                f"{actual_count} columns found (expected >= {expected_min_columns}). "
                f"Columns: {col_list}"
            ),
        }
    )
    return results


def null_rate_check(engine, table_name: str, threshold: float = NULL_RATE_THRESHOLD) -> list[dict]:
    """
    Computes the null rate per column and flags any column exceeding
    `threshold`. Run this AFTER the sentinel-value imputation step
    (Demo Piece 2) -- behavioral_features should be fully populated,
    so a failure here means the imputation step regressed or new
    columns were added without imputation logic.
    """
    results = []
    cols = get_columns(engine, table_name)
    if cols.empty:
        return results

    with engine.connect() as conn:
        total_rows = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()

    if not total_rows:
        results.append(
            {
                "table_name": table_name,
                "check_type": "null_rate",
                "column_name": None,
                "status": "FAIL",
                "details": "Table has 0 rows.",
            }
        )
        return results

    with engine.connect() as conn:
        for col in cols["column_name"]:
            null_count = conn.execute(
                text(f'SELECT COUNT(*) FROM {table_name} WHERE "{col}" IS NULL')
            ).scalar()
            null_rate = null_count / total_rows
            status = "PASS" if null_rate <= threshold else "FAIL"
            results.append(
                {
                    "table_name": table_name,
                    "check_type": "null_rate",
                    "column_name": col,
                    "status": status,
                    "details": f"{null_count}/{total_rows} nulls ({null_rate:.4%})",
                }
            )
    return results


def value_range_check(engine, table_name: str, ranges: dict) -> list[dict]:
    """
    For columns with a known valid range (e.g. behavioral scores should
    be 0-1), counts out-of-range rows. Skips columns that don't exist
    in this table rather than failing -- lets one range dict be reused
    across tables.

    Also skips columns whose ACTUAL database type is boolean (detected
    via information_schema, not assumed) -- a boolean column has no
    numeric range to violate (it's always exactly TRUE or FALSE by
    definition), and PostgreSQL rejects a boolean/integer comparison
    outright rather than silently coercing it. Confirmed live:
    id_number_exact_match in document_ocr_results is stored as a real
    boolean column (written by document_ocr.py's original Python
    True/False values), and querying it with "< 0 OR > 1" crashed with
    "operator does not exist: boolean < integer" -- this affects any
    column of that type, not just this one specific column, so the fix
    is general rather than a one-column special case.
    """
    results = []
    col_info = get_columns(engine, table_name)
    cols = set(col_info["column_name"].tolist())
    dtypes = dict(zip(col_info["column_name"], col_info["data_type"]))

    with engine.connect() as conn:
        for col, (low, high) in ranges.items():
            if col not in cols:
                continue

            if dtypes.get(col) == "boolean":
                results.append(
                    {
                        "table_name": table_name,
                        "check_type": "value_range",
                        "column_name": col,
                        "status": "PASS",
                        "details": "Column is a native boolean type -- always trivially "
                                   "in-range (TRUE/FALSE), numeric range check skipped.",
                    }
                )
                continue

            violation_count = conn.execute(
                text(
                    f'SELECT COUNT(*) FROM {table_name} '
                    f'WHERE "{col}" IS NOT NULL AND ("{col}" < :low OR "{col}" > :high)'
                ),
                {"low": low, "high": high},
            ).scalar()
            status = "PASS" if violation_count == 0 else "FAIL"
            results.append(
                {
                    "table_name": table_name,
                    "check_type": "value_range",
                    "column_name": col,
                    "status": status,
                    "details": f"{violation_count} rows outside [{low}, {high}]",
                }
            )
    return results


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_results(engine, results: list[dict]) -> None:
    if not results:
        return
    df = pd.DataFrame(results)
    df["run_timestamp"] = dt.datetime.now()
    df.to_sql(REPORT_TABLE, engine, if_exists="append", index=False)


def schema_check_optional(engine, table_name: str, expected_min_columns: int) -> list[dict]:
    """
    Like schema_check, but for tables that legitimately may not exist
    yet (e.g. a biometric script hasn't been run on this checkout).
    A missing table here is reported as an informational SKIP, not a
    FAIL -- this table's absence doesn't mean the DATA is bad, it means
    that pipeline stage simply hasn't been run yet. This distinction
    matters: a hard FAIL on every fresh checkout that hasn't run all 4
    biometric scripts would be a false alarm, not a real quality gate.
    """
    results = []
    cols = get_columns(engine, table_name)

    if cols.empty:
        results.append(
            {
                "table_name": table_name,
                "check_type": "schema",
                "column_name": None,
                "status": "SKIP",
                "details": f"Table '{table_name}' not found -- optional biometric result "
                           f"table, not yet populated on this checkout (run the "
                           f"corresponding biometric script first if this is unexpected).",
            }
        )
        return results

    actual_count = len(cols)
    status = "PASS" if actual_count >= expected_min_columns else "FAIL"
    col_list = ", ".join(cols["column_name"].tolist())
    results.append(
        {
            "table_name": table_name,
            "check_type": "schema",
            "column_name": None,
            "status": status,
            "details": (
                f"{actual_count} columns found (expected >= {expected_min_columns}). "
                f"Columns: {col_list}"
            ),
        }
    )
    return results


def run_biometric_table_checks(engine) -> list[dict]:
    """
    Runs schema, null-rate, and value-range checks against each
    biometric result table in BIOMETRIC_TABLES, skipping gracefully
    (not failing) for any table that doesn't exist yet on this
    checkout. Mirrors the same three-check structure already used for
    kyc_transactions/behavioral_features above, applied to the 4
    biometric sub-component tables.
    """
    all_results: list[dict] = []
    for table_name, config in BIOMETRIC_TABLES.items():
        schema_results = schema_check_optional(engine, table_name, config["expected_min_columns"])
        all_results += schema_results

        # If the table doesn't exist, schema_check_optional already
        # recorded a SKIP -- don't attempt null-rate/value-range checks
        # against a table that isn't there.
        table_missing = any(r["status"] == "SKIP" for r in schema_results)
        if table_missing:
            continue

        all_results += null_rate_check(engine, table_name)
        all_results += value_range_check(engine, table_name, config["value_ranges"])

    return all_results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stage 1 data quality checks")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="SQLAlchemy PostgreSQL URL")
    args = parser.parse_args()

    print("=" * 65)
    print("STAGE 1 -- Data Quality & Provenance Checks")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    engine = create_engine(args.db_url)

    with engine.connect() as conn:
        conn.execute(text(CREATE_REPORT_TABLE_SQL))
        conn.commit()

    all_results: list[dict] = []

    print("\n[1/3] Schema checks...")
    all_results += schema_check(engine, "kyc_transactions", expected_min_columns=32)
    all_results += schema_check(engine, "behavioral_features", expected_min_columns=6)

    print("[2/3] Null-rate checks...")
    all_results += null_rate_check(engine, "behavioral_features")

    print("[3/3] Value-range checks...")
    all_results += value_range_check(engine, "behavioral_features", VALUE_RANGES)

    print("\n[4/4] Biometric result table checks (schema, null-rate, value-range)...")
    print(f"      Checking: {', '.join(BIOMETRIC_TABLES.keys())}")
    all_results += run_biometric_table_checks(engine)

    write_results(engine, all_results)

    # Summary
    total = len(all_results)
    failed = [r for r in all_results if r["status"] == "FAIL"]
    skipped = [r for r in all_results if r["status"] == "SKIP"]
    passed = total - len(failed) - len(skipped)

    print("\n" + "=" * 65)
    print(f"RESULTS: {passed}/{total} checks passed, {len(failed)} failed, "
          f"{len(skipped)} skipped (optional tables not yet populated)")
    print(f"Full report written to '{REPORT_TABLE}' table")
    print("=" * 65)

    if skipped:
        print("\nSKIPPED (informational -- not a failure):")
        for r in skipped:
            print(f"  - {r['table_name']}: {r['details']}")

    if failed:
        print("\nFAILED CHECKS:")
        for r in failed:
            col = f" [{r['column_name']}]" if r["column_name"] else ""
            print(f"  - {r['table_name']}{col} ({r['check_type']}): {r['details']}")
        sys.exit(1)  # non-zero exit -- future CI/CD gate hook

    print("\n[PASS] All Stage 1 data quality checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
