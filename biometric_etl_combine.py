"""
biometric_etl_combine.py
Biometric ETL, Part 2 -- unify all 4 normalized biometric outputs
into a single, common-schema, feature-ready parquet file.

Addresses mid-sem evaluator feedback more fully: run AFTER
biometric_etl_normalize.py. That script normalizes each component's
own native schema individually (4 separate parquet files, each still
in its own column layout). This script takes those 4 already-
normalized outputs and maps each into ONE shared schema, so a single
file can actually be used as model-ready input -- which is what
"feature-ready" implies, rather than 4 differently-shaped files.

-----------------------------------------------------------------
SCOPE NOTE -- same honesty boundary as biometric_etl_normalize.py
-----------------------------------------------------------------
This still does NOT merge biometric data with real BAF applicants
row-for-row -- that dataset-level link does not exist (see report).
"Unified" here means unified ACROSS THE 4 BIOMETRIC VALIDATION SETS
-- one row per validation record (LFW pair, liveness image, synthetic
document, identity-mismatch case), each tagged with which component
and which real dataset it came from. It is not a per-applicant
biometric+behavioral feature table, because no dataset supports
building that honestly.

COMMON SCHEMA (one row per validation record, across all 4 sources):
    component            -- which biometric sub-component produced this row
                             (face_match | liveness | document_ocr | identity_mismatch)
    record_id             -- that component's own row identifier
    primary_score          -- the single most representative [0,1] score
                             for this record (defined per component below)
    primary_score_scaled  -- same value, re-scaled to [0,1] across the
                             FULL combined dataset (not just within one
                             component) -- this is what makes it usable
                             as one common feature column across sources
    outcome_label          -- ground truth or pass/fail label, where
                             available, normalized to 0/1
    source_table            -- which Postgres table this row came from
    etl_combined_at        -- timestamp this combination step ran

PRIMARY SCORE DEFINITION PER COMPONENT (documented, not hidden):
    face_match          -> predicted_match_score   (P(same person))
    liveness             -> predicted_liveness_score (P(real/live))
    document_ocr         -> ocr_mean_confidence / 100  (already 0-100 scale)
    identity_mismatch    -> name_similarity          (0-1 already)

Usage:
    python biometric_etl_combine.py
    python biometric_etl_combine.py --input-dir biometric_parquet --output-dir biometric_parquet
"""

import argparse
import os
from datetime import datetime, timezone

import pandas as pd

try:
    from provenance import log_provenance
    from db_config import get_engine
    _HAVE_PROVENANCE = True
except ImportError:
    _HAVE_PROVENANCE = False

INPUT_DIR_DEFAULT = "biometric_parquet"
OUTPUT_DIR_DEFAULT = "biometric_parquet"
COMBINED_FILENAME = "biometric_features_combined.parquet"


def _safe_read(path: str):
    if not os.path.exists(path):
        print(f"  SKIP -- {path} not found (run biometric_etl_normalize.py first)")
        return None
    df = pd.read_parquet(path)
    print(f"  Loaded {path} -- {len(df):,} rows")
    return df


def map_face_match(df: pd.DataFrame) -> pd.DataFrame:
    # Built via a dict literal, not column-by-column assignment on an
    # empty frame -- assigning a scalar to a column of a still-empty
    # DataFrame does NOT broadcast across rows added afterward (it
    # silently produces NaN). Passing a dict to the DataFrame
    # constructor broadcasts scalars correctly against any array-like
    # value present, which is what we want here.
    return pd.DataFrame({
        "component": "face_match",
        "record_id": df["pair_index"].astype(str),
        "primary_score": df["predicted_match_score"].astype(float),
        "outcome_label": df["true_label"].astype(float),  # 1 = same person
        "source_table": "face_match_results",
    })


def map_liveness(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "component": "liveness",
        "record_id": df["image_index"].astype(str),
        "primary_score": df["predicted_liveness_score"].astype(float),
        "outcome_label": df["true_label"].astype(float),  # 1 = real/live
        "source_table": "liveness_results",
    })


def map_document_ocr(df: pd.DataFrame) -> pd.DataFrame:
    # ocr_mean_confidence observed on a 0-100 scale from the live run;
    # divide to bring it onto the same 0-1 footing as the other scores.
    return pd.DataFrame({
        "component": "document_ocr",
        "record_id": df["document_id"].astype(str),
        "primary_score": df["ocr_mean_confidence"].astype(float) / 100.0,
        "outcome_label": df["id_number_exact_match"].astype(float),
        "source_table": "document_ocr_results",
    })


def map_identity_mismatch(df: pd.DataFrame) -> pd.DataFrame:
    # outcome: 1 = consistent/clean, 0 = a mismatch was present -- inverted
    # so "1" means "passed", matching the sense of the other 3 components'
    # outcome_label (1 = good/genuine outcome) for consistency.
    mismatch_flag = df["document_mismatch"].astype(float) + df["biometric_mismatch"].astype(float)
    return pd.DataFrame({
        "component": "identity_mismatch",
        "record_id": df["applicant_id"].astype(str),
        "primary_score": df["name_similarity"].astype(float),
        "outcome_label": (mismatch_flag == 0).astype(float),
        "source_table": "identity_mismatch_results",
    })


# Registered per-component mappers. Add an entry here if a new
# biometric sub-component is added to the project later.
COMPONENT_MAPPERS = {
    "face_match_results.parquet": map_face_match,
    "liveness_results.parquet": map_liveness,
    "document_ocr_results.parquet": map_document_ocr,
    "identity_mismatch_results.parquet": map_identity_mismatch,
}


def build_combined(input_dir: str) -> pd.DataFrame:
    frames = []
    print("Reading normalized per-component parquet files...")
    for filename, mapper_fn in COMPONENT_MAPPERS.items():
        path = os.path.join(input_dir, filename)
        df = _safe_read(path)
        if df is None:
            continue
        mapped = mapper_fn(df)
        frames.append(mapped)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)

    # Re-scale primary_score to [0,1] across the FULL combined dataset --
    # this is the step that makes it one genuinely comparable feature
    # column across sources, not just four locally-normalized ones.
    lo, hi = combined["primary_score"].min(), combined["primary_score"].max()
    if hi > lo:
        combined["primary_score_scaled"] = (combined["primary_score"] - lo) / (hi - lo)
    else:
        combined["primary_score_scaled"] = 0.0

    combined["etl_combined_at"] = datetime.now(timezone.utc).isoformat()

    # Stable column order
    combined = combined[[
        "component", "record_id", "primary_score", "primary_score_scaled",
        "outcome_label", "source_table", "etl_combined_at"
    ]]
    return combined


def main():
    parser = argparse.ArgumentParser(
        description="Combine the 4 normalized biometric parquet outputs into one feature-ready table."
    )
    parser.add_argument("--input-dir", default=INPUT_DIR_DEFAULT)
    parser.add_argument("--output-dir", default=OUTPUT_DIR_DEFAULT)
    args = parser.parse_args()

    print("=" * 65)
    print("BIOMETRIC ETL, PART 2 -- Combine into one feature-ready table")
    print("=" * 65)

    combined = build_combined(args.input_dir)

    if combined.empty:
        print("\n[WARNING] No component files were found -- nothing combined.")
        print("Run biometric_etl_normalize.py first, then rerun this script.")
        return 1

    os.makedirs(args.output_dir, exist_ok=True)
    out_path = os.path.join(args.output_dir, COMBINED_FILENAME)
    combined.to_parquet(out_path, index=False, engine="pyarrow")

    print("\n" + "=" * 65)
    print("COMBINED OUTPUT SUMMARY")
    print("=" * 65)
    print(f"Total rows        : {len(combined):,}")
    print("Rows per component:")
    print(combined["component"].value_counts().to_string())
    print(f"\nWrote combined feature-ready table -> {out_path}")
    print("=" * 65)

    if _HAVE_PROVENANCE:
        try:
            engine = get_engine()
            log_provenance(
                engine,
                script_name="biometric_etl_combine.py",
                source_dataset="face_match_results,liveness_results,document_ocr_results,identity_mismatch_results",
                target_table=out_path,
                row_count=len(combined),
                notes="Unified 4 biometric sub-components into one common-schema feature-ready parquet file.",
            )
        except Exception as e:
            print(f"(non-fatal) provenance logging skipped: {e}")

    print("\n[DONE] Combined biometric feature table complete.")
    return 0


if __name__ == "__main__":
    exit(main())
