"""
feature_engineering.py
Demo Piece 2 -- Behavioral Feature Engineering Pipeline

Reads raw onboarding records from kyc_transactions and derives
behavioral risk indicators into behavioral_features:

    session_velocity_score
    device_reuse_score
    address_stability_score
    identity_consistency_score
    geographic_risk_score
    financial_risk_score
    risk_anomaly_score        (composite, pre-model)

Column names below match the BAF ("Bank Account Fraud") Base
dataset. If your CSV uses different column names, adjust the
COLUMN MAP section at the top.

Usage:
    python feature_engineering.py
"""

import argparse
import numpy as np
import pandas as pd
from sqlalchemy import text
from db_config import get_engine
from biometric_features import synthesize_biometric_features
from provenance import log_provenance

SOURCE_TABLE = "kyc_transactions"
TARGET_TABLE = "behavioral_features"

# ---- COLUMN MAP: BAF Base dataset field names ----
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


def _minmax(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if hi - lo == 0:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - lo) / (hi - lo)


SENTINEL_COLS = [
    "prev_address_months_count",
    "current_address_months_count",
    "bank_months_count",
    "session_length_in_minutes",
    "device_distinct_emails_8w",
]


def load_raw(engine, sample_size: int = 25000, load_all: bool = False) -> pd.DataFrame:
    print(f"Reading transactions from PostgreSQL table '{SOURCE_TABLE}' ...", flush=True)
    query = f"SELECT * FROM {SOURCE_TABLE}"
    if not load_all and sample_size and sample_size > 0:
        query += f" LIMIT {sample_size}"
        print(f"  [sampling] Limiting to first {sample_size:,} rows for fast processing", flush=True)
    elif load_all:
        print("  [full mode] Loading all available rows from table", flush=True)
    df = pd.read_sql(query, engine)
    print(f"Loaded {len(df):,} rows", flush=True)
    return df


def clean_sentinels(df: pd.DataFrame) -> pd.DataFrame:
    """
    BAF encodes 'missing/unknown' as -1 in several columns (not a real
    negative value). Left as-is, these distort behavioral scores
    (e.g. address tenure of -1 months). Replace with NaN, then
    median-impute -- matches the approach used in the dissertation
    for resolving BAF's -1 sentinel issue.
    """
    print("Cleaning -1 sentinel values...", flush=True)
    for col in SENTINEL_COLS:
        if col in df.columns:
            n_sentinel = (df[col] == -1).sum()
            if n_sentinel:
                df[col] = df[col].replace(-1, np.nan)
                median_val = df[col].median()
                df[col] = df[col].fillna(median_val)
                print(f"  {col}: {n_sentinel:,} sentinels -> imputed with median ({median_val:.2f})", flush=True)
    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    print("Generating behavioral indicators...", flush=True)

    # 1. Session Velocity Score -- abnormal onboarding activity / burst detection
    out["session_velocity_score"] = _minmax(
        0.5 * df[COLS["velocity_6h"]].fillna(0)
        + 0.3 * df[COLS["velocity_24h"]].fillna(0)
        + 0.2 * df[COLS["velocity_4w"]].fillna(0)
    )
    print("  [OK] Session Velocity Score", flush=True)

    # 2. Device Reuse Score -- same device across multiple identities
    out["device_reuse_score"] = _minmax(
        df[COLS["device_distinct_emails_8w"]].fillna(0)
        + 5 * df[COLS["device_fraud_count"]].fillna(0)
    )
    print("  [OK] Device Reuse Score", flush=True)

    # 3. Address Stability Score -- higher = more stable (longer at address)
    addr_tenure = df[COLS["current_address_months_count"]].clip(lower=0).fillna(0)
    out["address_stability_score"] = _minmax(addr_tenure)
    print("  [OK] Address Stability Score", flush=True)

    # 4. Identity Consistency Score -- name/email similarity + valid contact channels
    out["identity_consistency_score"] = _minmax(
        df[COLS["name_email_similarity"]].fillna(0)
        + df[COLS["phone_home_valid"]].fillna(0)
        + df[COLS["phone_mobile_valid"]].fillna(0)
    )
    print("  [OK] Identity Consistency Score", flush=True)

    # 5. Geographic Risk Score -- foreign-origin / non-standard acquisition channel
    src_is_risky = (df[COLS["source"]] == "TELEAPP").astype(int) if COLS["source"] in df.columns else 0
    out["geographic_risk_score"] = _minmax(
        df[COLS["foreign_request"]].fillna(0).astype(float) + src_is_risky
    )
    print("  [OK] Geographic Risk Score", flush=True)

    # 6. Financial Risk Score -- credit risk vs income vs requested limit
    out["financial_risk_score"] = _minmax(
        df[COLS["credit_risk_score"]].fillna(0)
        + df[COLS["proposed_credit_limit"]].fillna(0) / (df[COLS["income"]].replace(0, np.nan).fillna(1))
    )
    print("  [OK] Financial Risk Score", flush=True)

    # 7. Composite Risk Score -- weighted aggregate (pre-model heuristic)
    #    This is the OFFICIAL behavioral-only composite, matching your
    #    original mid-sem report's baseline. Biometric blending is NOT
    #    folded in here -- see blend_biometric() below, which produces a
    #    separate, clearly-labeled experimental column instead.
    out["risk_anomaly_score"] = (
        0.25 * out["session_velocity_score"]
        + 0.20 * out["device_reuse_score"]
        + 0.15 * (1 - out["address_stability_score"])
        + 0.15 * (1 - out["identity_consistency_score"])
        + 0.10 * out["geographic_risk_score"]
        + 0.15 * out["financial_risk_score"]
    )
    print("  [OK] Composite Risk Score (behavioral-only -- this is your official baseline)", flush=True)

    # carry the label through for later evaluation (not used as a model input)
    if COLS["fraud_bool"] in df.columns:
        out["fraud_bool"] = df[COLS["fraud_bool"]]

    # Handle any residual inf/NaN via median imputation (per dissertation notes on BAF -1 sentinels)
    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.fillna(out.median(numeric_only=True))

    return out


def blend_biometric(out: pd.DataFrame) -> pd.DataFrame:
    """
    Adds Layer 5 biometric scores as a SEPARATE experimental column,
    clearly distinct from the official behavioral-only risk_anomaly_score.
    """
    out = synthesize_biometric_features(out, fraud_col="fraud_bool")
    out["risk_anomaly_score_experimental_with_biometric"] = (
        0.85 * out["risk_anomaly_score"] + 0.15 * out["biometric_risk_score"]
    )
    print("  [OK] Experimental biometric-blended score added (separate column, NOT the official baseline)", flush=True)
    return out


def write_features(df: pd.DataFrame, engine, chunksize: int = 5_000):
    total = len(df)
    print(f"Writing engineered features to '{TARGET_TABLE}' ({total:,} records) ...", flush=True)
    
    if total <= chunksize:
        df.to_sql(TARGET_TABLE, engine, if_exists="replace", index=False, chunksize=2_000, method="multi")
    else:
        for i, start in enumerate(range(0, total, chunksize)):
            chunk = df.iloc[start:start + chunksize]
            mode = "replace" if i == 0 else "append"
            chunk.to_sql(TARGET_TABLE, engine, if_exists=mode, index=False, chunksize=2_000, method="multi")
            written = min(start + chunksize, total)
            print(f"  [DB Write] Wrote chunk {i+1} ({written:,}/{total:,} rows)", flush=True)

    with engine.connect() as conn:
        conn.execute(text(f'ALTER TABLE {TARGET_TABLE} ADD COLUMN IF NOT EXISTS row_id SERIAL PRIMARY KEY;'))
        conn.commit()
    print(f"Output table: {TARGET_TABLE}", flush=True)
    print(f"Generated feature records: {len(df):,}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Demo Piece 2: Behavioral Feature Engineering Pipeline")
    parser.add_argument("--sample-size", type=int, default=25000, help="Row limit from kyc_transactions (default: 25,000)")
    parser.add_argument("--all", action="store_true", help="Process all rows from kyc_transactions")
    args = parser.parse_args()

    print("=" * 65, flush=True)
    print("DEMO PIECE 2 -- Behavioral Feature Engineering Pipeline", flush=True)
    print("=" * 65, flush=True)

    engine = get_engine()
    raw = load_raw(engine, sample_size=args.sample_size, load_all=args.all)
    raw = clean_sentinels(raw)
    features = engineer_features(raw)
    features = blend_biometric(features)
    write_features(features, engine)

    # Stage 1 -- provenance metadata: records source table, git commit,
    # and row count for this feature-engineering run.
    log_provenance(
        engine,
        script_name="feature_engineering.py",
        source_dataset=SOURCE_TABLE,
        target_table=TARGET_TABLE,
        row_count=len(features),
    )

    print("[demo2] PASS -- Behavioral features successfully generated.", flush=True)


if __name__ == "__main__":
    main()
