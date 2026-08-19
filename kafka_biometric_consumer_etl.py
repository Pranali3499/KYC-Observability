"""
kafka_biometric_consumer_etl.py
Real-Time Biometric ETL & Scoring Consumer
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Subscribes to 'kyc-biometric-events', validates JSON schema, computes
composite biometric risk indicators, writes output to PostgreSQL
'biometric_real_time_scores' table, and exports Prometheus metrics.

Responds directly to mid-sem evaluator feedback:
"Integrate Kafka topic for real-time biometric events; define schema and retention policy."

Usage:
  python kafka_biometric_consumer_etl.py
  python kafka_biometric_consumer_etl.py --max-messages 50
"""

import argparse
import json
import os
import time

import jsonschema
from confluent_kafka import Consumer, KafkaError
from prometheus_client import Counter, Histogram, start_http_server
from sqlalchemy import text

from db_config import get_engine

TOPIC_NAME = "kyc-biometric-events"
CONSUMER_GROUP = "kyc-biometric-consumer-group"
DEFAULT_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DEFAULT_METRICS_PORT = 8003
OUTPUT_TABLE = "biometric_real_time_scores"
SCHEMA_PATH = os.path.join("schemas", "biometric_event_schema.json")

# --- Prometheus Metrics ---
BIO_EVENTS_PROCESSED = Counter(
    "kyc_biometric_events_processed_total", "Total biometric verification events processed"
)
BIO_SPOOFS_FLAGGED = Counter(
    "kyc_biometric_spoofs_flagged_total", "Total biometric events flagged as suspected spoofs / high risk"
)
BIO_PROCESSING_ERRORS = Counter(
    "kyc_biometric_errors_total", "Total processing or schema validation errors in biometric stream"
)
BIO_PROCESSING_LATENCY = Histogram(
    "kyc_biometric_processing_latency_ms",
    "Per-event biometric ETL processing latency in milliseconds",
    buckets=(2, 5, 10, 20, 50, 100, 200, 500),
)

CREATE_BIO_OUTPUT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {OUTPUT_TABLE} (
    id SERIAL PRIMARY KEY,
    event_id VARCHAR(64),
    applicant_id VARCHAR(64),
    scored_at TIMESTAMP NOT NULL DEFAULT NOW(),
    face_match_score DOUBLE PRECISION,
    liveness_score DOUBLE PRECISION,
    ocr_confidence_score DOUBLE PRECISION,
    name_similarity DOUBLE PRECISION,
    document_type VARCHAR(32),
    device_hash VARCHAR(64),
    is_spoof_suspected BOOLEAN,
    biometric_risk_score DOUBLE PRECISION,
    biometric_outcome VARCHAR(32),
    processing_latency_ms DOUBLE PRECISION
);
"""


def load_schema():
    if os.path.exists(SCHEMA_PATH):
        with open(SCHEMA_PATH, "r") as f:
            return json.load(f)
    return None


def calculate_biometric_risk(event: dict) -> float:
    """
    Computes normalized biometric risk score [0, 1].
    Higher = higher risk / lower biometric confidence.
    """
    fm_risk = 1.0 - max(0.0, min(1.0, float(event.get("face_match_score", 0.5))))
    live_risk = 1.0 - max(0.0, min(1.0, float(event.get("liveness_score", 0.5))))
    ocr_risk = 1.0 - max(0.0, min(1.0, float(event.get("ocr_confidence_score", 50.0)) / 100.0))
    name_risk = 1.0 - max(0.0, min(1.0, float(event.get("name_similarity", 0.5))))

    risk_score = 0.35 * fm_risk + 0.35 * live_risk + 0.15 * ocr_risk + 0.15 * name_risk
    return round(float(risk_score), 4)


def write_biometric_score(engine, event: dict, risk_score: float, latency_ms: float):
    with engine.connect() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {OUTPUT_TABLE}
                    (event_id, applicant_id, face_match_score, liveness_score,
                     ocr_confidence_score, name_similarity, document_type,
                     device_hash, is_spoof_suspected, biometric_risk_score,
                     biometric_outcome, processing_latency_ms)
                VALUES
                    (:event_id, :applicant_id, :face_match_score, :liveness_score,
                     :ocr_confidence_score, :name_similarity, :document_type,
                     :device_hash, :is_spoof_suspected, :biometric_risk_score,
                     :biometric_outcome, :processing_latency_ms)
                """
            ),
            {
                "event_id": event.get("event_id"),
                "applicant_id": event.get("applicant_id"),
                "face_match_score": float(event.get("face_match_score", 0.0)),
                "liveness_score": float(event.get("liveness_score", 0.0)),
                "ocr_confidence_score": float(event.get("ocr_confidence_score", 0.0)),
                "name_similarity": float(event.get("name_similarity", 0.0)),
                "document_type": str(event.get("document_type", "UNKNOWN")),
                "device_hash": str(event.get("device_hash", "")),
                "is_spoof_suspected": bool(event.get("is_spoof_suspected", False)),
                "biometric_risk_score": risk_score,
                "biometric_outcome": str(event.get("biometric_outcome", "UNKNOWN")),
                "processing_latency_ms": latency_ms,
            },
        )
        conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Consume and score real-time biometric verification events")
    parser.add_argument("--bootstrap-servers", default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--max-messages", type=int, default=None)
    parser.add_argument("--metrics-port", type=int, default=DEFAULT_METRICS_PORT)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    print("=" * 65)
    print("REAL-TIME BIOMETRIC STREAMING CONSUMER & ETL")
    print("=" * 65)

    try:
        start_http_server(args.metrics_port)
        print(f"Prometheus metrics active on http://localhost:{args.metrics_port}/metrics")
    except Exception as e:
        print(f"Metrics server note: {e}")

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text(CREATE_BIO_OUTPUT_TABLE_SQL))
        conn.commit()

    schema = load_schema()
    if schema:
        print("Loaded biometric JSON schema for incoming event validation.")

    consumer = Consumer({
        "bootstrap.servers": args.bootstrap_servers,
        "group.id": CONSUMER_GROUP,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([TOPIC_NAME])

    print(f"Subscribed to topic '{TOPIC_NAME}'. Listening...")
    processed = 0

    try:
        while True:
            if args.max_messages and processed >= args.max_messages:
                break

            msg = consumer.poll(timeout=args.timeout)
            if msg is None:
                if args.max_messages:
                    print(f"Processed {processed}/{args.max_messages} events -- exiting.")
                    break
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(f"  [ERROR] Kafka error: {msg.error()}")
                BIO_PROCESSING_ERRORS.inc()
                continue

            start = time.perf_counter()

            try:
                event = json.loads(msg.value().decode("utf-8"))

                # Schema validation
                if schema:
                    jsonschema.validate(instance=event, schema=schema)

                risk_score = calculate_biometric_risk(event)
                latency_ms = (time.perf_counter() - start) * 1000

                write_biometric_score(engine, event, risk_score, latency_ms)

                BIO_EVENTS_PROCESSED.inc()
                if event.get("is_spoof_suspected") or risk_score > 0.40:
                    BIO_SPOOFS_FLAGGED.inc()
                BIO_PROCESSING_LATENCY.observe(latency_ms)

                processed += 1
                if processed % 10 == 0 or processed == args.max_messages:
                    print(f"  Processed {processed} events | Last: {event.get('applicant_id')} -> Risk: {risk_score:.4f} (Latency: {latency_ms:.2f}ms)")

            except jsonschema.ValidationError as ve:
                print(f"  [SCHEMA ERROR] Invalid biometric event: {ve.message}")
                BIO_PROCESSING_ERRORS.inc()
            except Exception as e:
                print(f"  [ERROR] Failed processing biometric event: {e}")
                BIO_PROCESSING_ERRORS.inc()

    except KeyboardInterrupt:
        print("\nConsumer stopped by user.")
    finally:
        consumer.close()
        print(f"[DONE] Biometric consumer completed. Total processed: {processed}")
        if args.max_messages and processed > 0:
            print(f"Metrics server active at http://localhost:{args.metrics_port}/metrics.")


if __name__ == "__main__":
    main()
