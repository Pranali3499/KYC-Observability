"""
biometric_etl_normalize.py
Biometric ETL -- normalize sub-component outputs into a common
schema and write feature-ready parquet files.

Addresses mid-sem evaluator feedback: "Build ETL to normalize
biometric formats and produce feature-ready parquet files."

-----------------------------------------------------------------
SCOPE NOTE -- read this before treating the output as more than it is
-----------------------------------------------------------------
This ETL normalizes and exports VALIDATION-SET-LEVEL results from
each biometric sub-component. It does NOT merge them into one
row-per-real-applicant table, because no dataset in this project
links a real KYC applicant's onboarding record, ID document, and
biometric sample together -- the dissertation report explains this
directly (Section on biometric independence: "no public dataset
links a real applicant's claimed identity, government-issued
document, and biometric sample"). Forcing a per-applicant merge
here would fabricate a relationship that does not exist in the
underlying data.

Each output parquet file's rows correspond to that sub-component's
OWN validation records (e.g. one row per synthetic ID document
tested, one row per identity-mismatch scenario tested) -- not to a
BAF kyc_transactions.row_id. Treat this as "the same normalization
and export mechanism a real per-applicant biometric pipeline would
use," demonstrated honestly on the data that actually exists.

-----------------------------------------------------------------
WHAT THIS SCRIPT CANNOT VERIFY ON ITS OWN
-----------------------------------------------------------------
face_match_results / liveness_results tables are checked for
existence defensively. Based on the terminal output captured from
running biometric_face_matching.py and biometric_liveness_detection.py,
those two scripts print AUC/FAR/FRR summary statistics and save a
model file (.pkl) + an ROC curve image -- no per-record results
table write was observed in that output. If those tables don't
exist on your machine either, this script will say so clearly and
skip them rather than fail or fabricate data for them. If your
actual scripts DO write such a table under a different name, edit
the SOURCE_TABLES list below to match.

Usage:
    python biometric_etl_normalize.py
    python biometric_etl_normalize.py --output-dir biometric_parquet
    python biometric_etl_normalize.py --db-url postgresql://user:pass@localhost:5432/kyc_db
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, inspect

try:
    from db_config import DB_URL as _DEFAULT_DB_URL
except ImportError:
    _DEFAULT_DB_URL = os.environ.get(
        "KYC_DB_URL", "postgresql://kyc_user:kyc_pass@localhost:5432/kyc_db"
    )

try:
    from provenance import log_provenance
    _HAVE_PROVENANCE = True
except ImportError:
    _HAVE_PROVENANCE = False

OUTPUT_DIR_DEFAULT = "biometric_parquet"

# Candidate source tables. Each is checked for existence before
# being queried -- nothing here is assumed to exist just because a
# script name suggests it should have written a table.
SOURCE_TABLES = [
    "document_ocr_results",       # confirmed to exist from a live run
    "identity_mismatch_results",  # confirmed to exist from a live run
    "face_match_results",         # NOT confirmed -- checked defensively
    "liveness_results",           # NOT confirmed -- checked defensively
]


def get_engine(db_url: str):
    return create_engine(db_url)


def table_exists(engine, table_name: str) -> bool:
    insp = inspect(engine)
    return insp.has_table(table_name)


def normalize_table(df: pd.DataFrame, source_table: str) -> pd.DataFrame:
    """
    Generic normalization applied to ANY biometric results table,
    regardless of its specific columns:
      1. Every numeric column gets a companion "<col>_scaled" column,
         min-max normalized to [0,1] -- putting every biometric
         sub-component's numeric outputs on the same common scale,
         which is the literal ask in "normalize biometric formats."
      2. Metadata columns are added: which component produced this
         row, which table it came from, and when this ETL run
         processed it -- this is the provenance trail a real
         feature-ready dataset needs.
      3. Original columns are preserved unchanged alongside the
         scaled versions, so no information is lost in translation.
    """
    out = df.copy()

    numeric_cols = out.select_dtypes(include=["number"]).columns.tolist()
    for col in numeric_cols:
        col_min = out[col].min()
        col_max = out[col].max()
        if pd.isna(col_min) or pd.isna(col_max) or col_max == col_min:
            out[f"{col}_scaled"] = 0.0
        else:
            out[f"{col}_scaled"] = (out[col] - col_min) / (col_max - col_min)

    out["source_biometric_component"] = source_table
    out["etl_source_table"] = source_table
    out["etl_processed_at"] = datetime.now(timezone.utc).isoformat()

    return out


def run_etl(db_url: str, output_dir: str):
    print("=" * 65)
    print("BIOMETRIC ETL -- Normalize sub-component outputs to parquet")
    print("=" * 65)

    engine = get_engine(db_url)
    os.makedirs(output_dir, exist_ok=True)

    found_tables = []
    missing_tables = []
    manifest_rows = []

    for table_name in SOURCE_TABLES:
        print(f"\nChecking for table '{table_name}' ...")
        if not table_exists(engine, table_name):
            print(f"  NOT FOUND -- skipping. (Only aggregate summary metrics "
                  f"were observed for this component in prior runs, not a "
                  f"per-record table. If your version of the project does "
                  f"write this table under a different name, add it to "
                  f"SOURCE_TABLES at the top of this script.)")
            missing_tables.append(table_name)
            continue

        df = pd.read_sql(f"SELECT * FROM {table_name}", engine)
        print(f"  FOUND -- {len(df):,} rows, {df.shape[1]} columns")
        print(f"  Columns: {', '.join(df.columns.tolist())}")

        normalized = normalize_table(df, table_name)

        out_path = os.path.join(output_dir, f"{table_name}.parquet")
        normalized.to_parquet(out_path, index=False, engine="pyarrow")
        print(f"  Wrote {len(normalized):,} rows -> {out_path}")

        found_tables.append(table_name)
        manifest_rows.append({
            "source_table": table_name,
            "row_count": len(normalized),
            "column_count": normalized.shape[1],
            "output_file": out_path,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        })

    # Manifest: a single small parquet + printed summary describing
    # what this ETL run actually found and processed -- this IS the
    # audit trail / provenance artifact for this step.
    manifest_path = os.path.join(output_dir, "_etl_manifest.parquet")
    if manifest_rows:
        pd.DataFrame(manifest_rows).to_parquet(manifest_path, index=False, engine="pyarrow")

    print("\n" + "=" * 65)
    print("ETL SUMMARY")
    print("=" * 65)
    print(f"Tables processed : {len(found_tables)} -> {found_tables}")
    print(f"Tables not found : {len(missing_tables)} -> {missing_tables}")
    print(f"Output directory : {os.path.abspath(output_dir)}")
    if manifest_rows:
        print(f"Manifest written : {manifest_path}")
    print("=" * 65)

    if not found_tables:
        print("\n[WARNING] No source tables were found. Nothing was written.")
        print("Run document_ocr.py and identity_mismatch_detection.py first")
        print("(or whichever biometric scripts populate results tables on")
        print("your machine), then rerun this ETL.")
        return 1

    if _HAVE_PROVENANCE:
        try:
            log_provenance(
                engine,
                script_name="biometric_etl_normalize.py",
                source_dataset=",".join(found_tables),
                target_table=output_dir,
                row_count=sum(m["row_count"] for m in manifest_rows),
                notes=f"Normalized {len(found_tables)} biometric result "
                      f"table(s) to parquet; {len(missing_tables)} "
                      f"candidate table(s) not present.",
            )
        except Exception as e:
            print(f"(non-fatal) provenance logging skipped: {e}")

    print("\n[DONE] Biometric ETL complete.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Normalize biometric sub-component outputs into feature-ready parquet files."
    )
    parser.add_argument("--db-url", default=_DEFAULT_DB_URL)
    parser.add_argument("--output-dir", default=OUTPUT_DIR_DEFAULT,
                         help="Directory to write parquet files into (created if missing).")
    args = parser.parse_args()

    exit_code = run_etl(args.db_url, args.output_dir)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
