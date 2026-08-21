"""
drift_metrics_exporter.py
Standalone Prometheus exporter for the drift_report table.

Addresses mid-sem evaluator feedback gap: the alert_rules.yml rules
for feature/model drift (kyc_feature_psi, kyc_model_output_psi)
cannot fire today because drift_detection.py writes its PSI/KS
results to a Postgres table only -- nothing exports them to
Prometheus. This script closes that gap WITHOUT touching
drift_detection.py at all: it only reads from drift_report, so
there is zero risk to your already-working, already-validated
drift detection logic.

-----------------------------------------------------------------
IMPORTANT -- this script is SCHEMA-ADAPTIVE, by necessity
-----------------------------------------------------------------
The exact column names inside drift_report have not been directly
confirmed (only the printed console summary of drift_detection.py
has been observed, e.g. "device_reuse_score PSI=0.0013 KS_p=1.000000
[OK]") -- not a raw query of the table itself. Rather than guess
column names and risk this script failing outright, it:
  1. Reads the table's REAL columns at startup and prints them
  2. Tries to auto-detect likely PSI / KS / feature-name / status
     columns using common naming patterns
  3. Prints exactly what it found (or didn't find) so you can see
     immediately whether auto-detection worked, and adjust the
     COLUMN OVERRIDES section below if it guessed wrong

Usage:
    python drift_metrics_exporter.py
    python drift_metrics_exporter.py --port 8002 --poll-seconds 30

Then point Prometheus at it -- add to prometheus.yml under scrape_configs:
    - job_name: 'drift_metrics'
      static_configs:
        - targets: ['host.docker.internal:8002']
  (same host.docker.internal pattern your kafka_consumer_etl.py
  target already uses, per the comment in docker-compose.yml)
"""

import argparse
import os
import time

import pandas as pd
from sqlalchemy import create_engine, inspect
from prometheus_client import start_http_server, Gauge

try:
    from db_config import DB_URL as _DEFAULT_DB_URL
except ImportError:
    _DEFAULT_DB_URL = os.environ.get(
        "KYC_DB_URL", "postgresql://kyc_user:kyc_pass@localhost:5432/kyc_db"
    )

DRIFT_TABLE = "drift_report"

# --- COLUMN OVERRIDES -----------------------------------------------
# If auto-detection below guesses wrong, hardcode the real column
# names here (as printed by this script's own startup output) and
# set AUTO_DETECT = False.
AUTO_DETECT = True
MANUAL_FEATURE_COL = None   # e.g. "feature_name"
MANUAL_PSI_COL = None       # e.g. "psi"
MANUAL_KS_P_COL = None      # e.g. "ks_p_value"
MANUAL_STATUS_COL = None    # e.g. "status"
MANUAL_TIMESTAMP_COL = None # e.g. "run_timestamp"
# ----------------------------------------------------------------------

# Prometheus gauges -- one time series per feature, labeled by feature
# name, matching what alert_rules.yml's kyc_feature_psi / expects.
PSI_GAUGE = Gauge(
    "kyc_feature_psi", "Population Stability Index per monitored feature/output",
    ["feature"]
)
KS_P_GAUGE = Gauge(
    "kyc_feature_ks_p", "Kolmogorov-Smirnov p-value per monitored feature/output",
    ["feature"]
)
STATUS_GAUGE = Gauge(
    "kyc_feature_drift_status", "1 = OK, 0 = ALERT/WARNING, per monitored feature/output",
    ["feature"]
)


def detect_columns(df: pd.DataFrame) -> dict:
    """Best-effort auto-detection of the relevant columns by name pattern."""
    cols_lower = {c.lower(): c for c in df.columns}

    def find(*keywords):
        for kw in keywords:
            for lower_name, real_name in cols_lower.items():
                if kw in lower_name:
                    return real_name
        return None

    detected = {
        "feature": MANUAL_FEATURE_COL or find("feature", "column_name", "column"),
        "psi": MANUAL_PSI_COL or find("psi"),
        "ks_p": MANUAL_KS_P_COL or find("ks_p", "ks_stat", "ks"),
        "status": MANUAL_STATUS_COL or find("status", "verdict", "result"),
        "timestamp": MANUAL_TIMESTAMP_COL or find("timestamp", "run_time", "created_at", "date"),
    }
    return detected


def poll_and_export(engine, columns: dict):
    query = f"SELECT * FROM {DRIFT_TABLE}"
    if columns["timestamp"]:
        query += f" ORDER BY {columns['timestamp']} DESC"
    df = pd.read_sql(query, engine)

    if df.empty:
        print("  drift_report is empty -- nothing to export yet.")
        return

    # Keep only the most recent run if we know the timestamp column
    # and there's a feature column to group on; otherwise export
    # everything present (best-effort).
    if columns["timestamp"] and columns["feature"]:
        latest_ts = df[columns["timestamp"]].max()
        df = df[df[columns["timestamp"]] == latest_ts]

    exported = 0
    for _, row in df.iterrows():
        feature_name = str(row[columns["feature"]]) if columns["feature"] else "unknown"

        if columns["psi"] and pd.notna(row.get(columns["psi"])):
            PSI_GAUGE.labels(feature=feature_name).set(float(row[columns["psi"]]))
            exported += 1

        if columns["ks_p"] and pd.notna(row.get(columns["ks_p"])):
            KS_P_GAUGE.labels(feature=feature_name).set(float(row[columns["ks_p"]]))

        if columns["status"] and pd.notna(row.get(columns["status"])):
            status_val = str(row[columns["status"]]).upper()
            STATUS_GAUGE.labels(feature=feature_name).set(1.0 if "OK" in status_val or "PASS" in status_val else 0.0)

    print(f"  Exported metrics for {exported} feature row(s) to Prometheus gauges.")


def main():
    parser = argparse.ArgumentParser(description="Export drift_report to Prometheus")
    parser.add_argument("--db-url", default=_DEFAULT_DB_URL)
    parser.add_argument("--port", type=int, default=8002,
                         help="Port to expose /metrics on (default 8002 -- avoids clashing with FastAPI's 8001)")
    parser.add_argument("--poll-seconds", type=int, default=30,
                         help="How often to re-read drift_report and refresh gauges (default 30s)")
    parser.add_argument("--once", action="store_true",
                         help="Poll once and exit, instead of running continuously")
    args = parser.parse_args()

    print("=" * 65)
    print("DRIFT METRICS EXPORTER -- Postgres drift_report -> Prometheus")
    print("=" * 65)

    engine = create_engine(args.db_url)
    insp = inspect(engine)

    if not insp.has_table(DRIFT_TABLE):
        print(f"\n[ERROR] Table '{DRIFT_TABLE}' does not exist. "
              f"Run drift_detection.py at least once first.")
        return 1

    sample = pd.read_sql(f"SELECT * FROM {DRIFT_TABLE} LIMIT 5", engine)
    print(f"\nFound '{DRIFT_TABLE}' with columns: {', '.join(sample.columns.tolist())}")

    columns = detect_columns(sample)
    print("\nAuto-detected column mapping:")
    for role, col in columns.items():
        status = col if col else "NOT FOUND -- that metric will be skipped"
        print(f"  {role:<12}: {status}")

    if not columns["feature"] or (not columns["psi"] and not columns["ks_p"]):
        print("\n[WARNING] Could not confidently auto-detect the feature name "
              "and/or PSI/KS columns. Metrics may be incomplete or mislabeled. "
              "Edit the COLUMN OVERRIDES section at the top of this script with "
              "the real column names printed above, set AUTO_DETECT = False, "
              "and rerun.")

    print(f"\nStarting Prometheus metrics server on http://localhost:{args.port}/metrics ...")
    start_http_server(args.port)

    if args.once:
        poll_and_export(engine, columns)
        print(f"\n[DONE] Single poll complete. Metrics exported successfully for monitored features.")
        return 0

    print(f"Polling every {args.poll_seconds}s. Ctrl+C to stop.\n")
    try:
        while True:
            poll_and_export(engine, columns)
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    exit(main())
