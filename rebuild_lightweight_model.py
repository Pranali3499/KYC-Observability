"""
rebuild_lightweight_model.py
Rebuilds isolation_forest_tuned.pkl as a fast, lightweight compressed model artifact (~1.5 MB)
with max_samples=256 and compress=3, preventing OOM in CI/CD and Jenkins environments.
"""

import os
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

FEATURE_COLS = [
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

OUTPUT_PATH = "isolation_forest_tuned.pkl"


def _minmax(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if hi - lo == 0:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - lo) / (hi - lo)


def clean_sentinels(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
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


def main():
    print("Loading sample data to train lightweight tuned model...")
    if os.path.exists("ci_test_data.csv"):
        df = pd.read_csv("ci_test_data.csv")
    elif os.path.exists("Base.csv"):
        df = pd.read_csv("Base.csv", nrows=50000)
    else:
        raise FileNotFoundError("Neither Base.csv nor ci_test_data.csv found.")

    cleaned = clean_sentinels(df)
    feats = engineer_features_from_df(cleaned)
    X = feats[FEATURE_COLS]

    print(f"Fitting IsolationForest on {len(X):,} samples with max_samples=256...")
    model = IsolationForest(
        n_estimators=170,
        max_samples=256,
        contamination=0.02614,
        max_features=0.8,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X)

    # Save compressed
    joblib.dump(model, OUTPUT_PATH, compress=3)
    size_mb = os.path.getsize(OUTPUT_PATH) / (1024 * 1024)
    print(f"Successfully saved '{OUTPUT_PATH}' ({size_mb:.2f} MB)")
    print(f"Model properties: n_estimators={model.n_estimators}, contamination={model.contamination:.4f}")


if __name__ == "__main__":
    main()
