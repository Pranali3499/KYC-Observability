"""
kafka_consumer_etl.py
Stage 3 -- MVI Real-Time Skeleton: Consumer / ETL / Scoring
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Completes the MVI chain: Kafka -> ETL -> feature store -> model.

Subscribes to kyc-onboarding-events, and for each incoming event:
  1. Engineers the same 6 behavioral features as feature_engineering.py
     (Demo Piece 2), applied to a single record instead of a 1M-row batch
  2. Scores it with the tuned Isolation Forest (isolation_forest_tuned.pkl
     from Stage 2) to get a real-time anomaly score
  3. Writes the result to a new real_time_scores table in PostgreSQL --
     this is the "feature store" output for the streaming path, kept
     separate from the batch behavioral_features table so the two
     pipelines (batch vs streaming) don't collide

This is intentionally a THIN vertical slice, not a hardened production
consumer -- no retry/dead-letter handling, no schema registry yet
(that's flagged as a later hardening item). The goal here is proving
the end-to-end chain works, per the "deliver an MVI, then harden"
guidance from the evaluator feedback.

Usage:
    python kafka_consumer_etl.py
    python kafka_consumer_etl.py --max-messages 20   (process N then exit,
                                                        useful for testing)

Requires:
    pip install confluent-kafka joblib
"""

import argparse
import json
import time

import joblib
import numpy as np
import pandas as pd
from confluent_kafka import Consumer, KafkaError
from sqlalchemy import text

from db_config import get_engine
from provenance import log_provenance

TOPIC_NAME = "kyc-onboarding-events"
CONSUMER_GROUP = "kyc-etl-consumer-group"
DEFAULT_BOOTSTRAP = "localhost:9092"

TUNED_MODEL_PATH = "isolation_forest_tuned.pkl"
OUTPUT_TABLE = "real_time_scores"

FEATURE_COLUMNS = [
    "session_velocity_score",
    "device_reuse_score",
    "address_stability_score",
    "identity_consistency_score",
    "geographic_risk_score",
    "financial_risk_score",
]

CREATE_OUTPUT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {OUTPUT_TABLE} (
    id SERIAL PRIMARY KEY,
    row_id BIGINT,
    scored_at TIMESTAMP NOT NULL DEFAULT NOW(),
    session_velocity_score DOUBLE PRECISION,
    device_reuse_score DOUBLE PRECISION,
    address_stability_score DOUBLE PRECISION,
    identity_consistency_score DOUBLE PRECISION,
    geographic_risk_score DOUBLE PRECISION,
    financial_risk_score DOUBLE PRECISION,
    anomaly_score DOUBLE PRECISION,
    is_anomaly BOOLEAN,
    fraud_bool INTEGER,
    inference_latency_ms DOUBLE PRECISION
);
"""


def load_model():
    print(f"Loading tuned model from '{TUNED_MODEL_PATH}'...")
    model = joblib.load(TUNED_MODEL_PATH)
    print("Model loaded.")
    return model


def compute_feature_ranges(engine) -> dict:
    """
    Computes the min/max of each RAW (pre-normalization) feature score
    across the full kyc_transactions table -- the same combined-formula
    values that feature_engineering.py's _minmax() scales over in the
    batch pipeline. Running this once at consumer startup (not guessing
    fixed bounds) means real-time scoring uses the actual training-data
    scale, not an approximation -- this replaces an earlier hardcoded
    version that mis-scaled several features and pushed every event to
    look anomalous.
    """
    print("Computing feature normalization ranges from kyc_transactions (one-time, at startup)...")
    query = """
        SELECT
            MIN(0.5*velocity_6h + 0.3*velocity_24h + 0.2*velocity_4w) AS vel_min,
            MAX(0.5*velocity_6h + 0.3*velocity_24h + 0.2*velocity_4w) AS vel_max,
            MIN(device_distinct_emails_8w + 5*device_fraud_count) AS dev_min,
            MAX(device_distinct_emails_8w + 5*device_fraud_count) AS dev_max,
            MIN(GREATEST(current_address_months_count, 0)) AS addr_min,
            MAX(GREATEST(current_address_months_count, 0)) AS addr_max,
            MIN(name_email_similarity + phone_home_valid::int + phone_mobile_valid::int) AS ident_min,
            MAX(name_email_similarity + phone_home_valid::int + phone_mobile_valid::int) AS ident_max,
            MIN(foreign_request::int + (CASE WHEN source = 'TELEAPP' THEN 1 ELSE 0 END)) AS geo_min,
            MAX(foreign_request::int + (CASE WHEN source = 'TELEAPP' THEN 1 ELSE 0 END)) AS geo_max,
            MIN(credit_risk_score + proposed_credit_limit / NULLIF(income, 0)) AS fin_min,
            MAX(credit_risk_score + proposed_credit_limit / NULLIF(income, 0)) AS fin_max
        FROM kyc_transactions
        WHERE income IS NOT NULL AND income != 0
    """
    with engine.connect() as conn:
        row = conn.execute(text(query)).fetchone()

    ranges = {
        "session_velocity": (float(row.vel_min), float(row.vel_max)),
        "device_reuse": (float(row.dev_min), float(row.dev_max)),
        "address_stability": (float(row.addr_min), float(row.addr_max)),
        "identity_consistency": (float(row.ident_min), float(row.ident_max)),
        "geographic_risk": (float(row.geo_min), float(row.geo_max)),
        "financial_risk": (float(row.fin_min), float(row.fin_max)),
    }
    print("Computed ranges:")
    for k, (lo, hi) in ranges.items():
        print(f"  {k}: [{lo:.2f}, {hi:.2f}]")
    return ranges


def engineer_features_single(event: dict, ranges: dict) -> dict:
    """
    Same logic as feature_engineering.py's engineer_features(), applied
    to a single event dict instead of a DataFrame batch. Normalization
    uses ranges computed from the real training data (see
    compute_feature_ranges), not guessed fixed bounds -- this is a
    documented simplification vs. the batch pipeline in one remaining
    way: ranges are computed once at consumer startup rather than
    updated as new data arrives, so a real production system would
    need a periodic refresh or a persisted scaler artifact from
    training. For this MVI, computing them once per run is sufficient
    to prove the chain works correctly.
    """
    def safe_float(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    velocity_6h = safe_float(event.get("velocity_6h"))
    velocity_24h = safe_float(event.get("velocity_24h"))
    velocity_4w = safe_float(event.get("velocity_4w"))
    session_velocity_raw = 0.5 * velocity_6h + 0.3 * velocity_24h + 0.2 * velocity_4w

    device_emails = safe_float(event.get("device_distinct_emails_8w"))
    device_fraud = safe_float(event.get("device_fraud_count"))
    device_reuse_raw = device_emails + 5 * device_fraud

    addr_tenure = max(safe_float(event.get("current_address_months_count")), 0)

    name_email_sim = safe_float(event.get("name_email_similarity"))
    phone_home = safe_float(event.get("phone_home_valid"))
    phone_mobile = safe_float(event.get("phone_mobile_valid"))
    identity_raw = name_email_sim + phone_home + phone_mobile

    foreign_request = safe_float(event.get("foreign_request"))
    src_risky = 1.0 if event.get("source") == "TELEAPP" else 0.0
    geo_raw = foreign_request + src_risky

    credit_risk = safe_float(event.get("credit_risk_score"))
    proposed_limit = safe_float(event.get("proposed_credit_limit"))
    income = safe_float(event.get("income"), default=1.0) or 1.0
    financial_raw = credit_risk + (proposed_limit / income)

    # Normalization using ranges computed from real training data
    # (see compute_feature_ranges), not guessed fixed bounds.
    def clip01(x, lo, hi):
        if hi - lo == 0:
            return 0.0
        return min(max((x - lo) / (hi - lo), 0.0), 1.0)

    features = {
        "session_velocity_score": clip01(session_velocity_raw, *ranges["session_velocity"]),
        "device_reuse_score": clip01(device_reuse_raw, *ranges["device_reuse"]),
        "address_stability_score": clip01(addr_tenure, *ranges["address_stability"]),
        "identity_consistency_score": clip01(identity_raw, *ranges["identity_consistency"]),
        "geographic_risk_score": clip01(geo_raw, *ranges["geographic_risk"]),
        "financial_risk_score": clip01(financial_raw, *ranges["financial_risk"]),
    }
    return features


def write_score(engine, row_id, features: dict, anomaly_score: float,
                 is_anomaly: bool, fraud_bool, latency_ms: float):
    with engine.connect() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {OUTPUT_TABLE}
                    (row_id, session_velocity_score, device_reuse_score,
                     address_stability_score, identity_consistency_score,
                     geographic_risk_score, financial_risk_score,
                     anomaly_score, is_anomaly, fraud_bool, inference_latency_ms)
                VALUES
                    (:row_id, :session_velocity_score, :device_reuse_score,
                     :address_stability_score, :identity_consistency_score,
                     :geographic_risk_score, :financial_risk_score,
                     :anomaly_score, :is_anomaly, :fraud_bool, :inference_latency_ms)
                """
            ),
            {
                "row_id": row_id,
                **features,
                "anomaly_score": anomaly_score,
                "is_anomaly": is_anomaly,
                "fraud_bool": fraud_bool,
                "inference_latency_ms": latency_ms,
            },
        )
        conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Stage 3: Kafka consumer -- real-time ETL + scoring")
    parser.add_argument("--bootstrap-servers", default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--max-messages", type=int, default=None,
                         help="Process this many messages then exit (omit to run continuously)")
    parser.add_argument("--timeout", type=float, default=10.0,
                         help="Seconds to wait for a message before giving up (continuous mode ignores this after first message)")
    args = parser.parse_args()

    print("=" * 65)
    print("STAGE 3 -- Kafka Consumer (Real-Time ETL + Scoring)")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    model = load_model()
    engine = get_engine()

    with engine.connect() as conn:
        conn.execute(text(CREATE_OUTPUT_TABLE_SQL))
        conn.commit()

    ranges = compute_feature_ranges(engine)

    consumer = Consumer({
        "bootstrap.servers": args.bootstrap_servers,
        "group.id": CONSUMER_GROUP,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([TOPIC_NAME])

    print(f"\nSubscribed to '{TOPIC_NAME}'. Waiting for messages...")
    if args.max_messages:
        print(f"Will process {args.max_messages} messages then exit.\n")
    else:
        print("Running continuously -- press Ctrl+C to stop.\n")

    processed = 0
    anomalies_found = 0

    try:
        while True:
            if args.max_messages and processed >= args.max_messages:
                break

            msg = consumer.poll(timeout=args.timeout)
            if msg is None:
                if args.max_messages:
                    print(f"No more messages after {args.timeout}s -- stopping "
                          f"({processed}/{args.max_messages} processed).")
                    break
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(f"  [ERROR] Kafka error: {msg.error()}")
                continue

            start = time.perf_counter()

            event = json.loads(msg.value().decode("utf-8"))
            row_id = event.get("row_id")
            fraud_bool = event.get("fraud_bool")

            features = engineer_features_single(event, ranges)
            X = pd.DataFrame([features])[FEATURE_COLUMNS]

            anomaly_score = float(-model.score_samples(X)[0])
            is_anomaly = bool(model.predict(X)[0] == -1)

            latency_ms = (time.perf_counter() - start) * 1000

            write_score(engine, row_id, features, anomaly_score, is_anomaly, fraud_bool, latency_ms)

            processed += 1
            if is_anomaly:
                anomalies_found += 1

            flag = "ANOMALY" if is_anomaly else "normal"
            print(f"  [{processed}] row_id={row_id}  score={anomaly_score:.4f}  "
                  f"[{flag}]  latency={latency_ms:.2f}ms")

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        consumer.close()

    log_provenance(
        engine,
        script_name="kafka_consumer_etl.py",
        source_dataset=TOPIC_NAME,
        target_table=OUTPUT_TABLE,
        row_count=processed,
        notes=f"{anomalies_found} anomalies flagged out of {processed} processed",
    )

    print("\n" + "=" * 65)
    print("CONSUMER SUMMARY")
    print("=" * 65)
    print(f"Messages processed: {processed}")
    print(f"Anomalies flagged:  {anomalies_found}")
    print(f"Results written to: '{OUTPUT_TABLE}' table")
    print("=" * 65)


if __name__ == "__main__":
    main()
