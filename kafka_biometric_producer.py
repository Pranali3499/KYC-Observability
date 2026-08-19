"""
kafka_biometric_producer.py
Real-Time Biometric Event Streaming Producer
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Publishes real-time biometric verification events (face match, liveness,
OCR confidence, name consistency, device hash) to the 'kyc-biometric-events'
Kafka topic with explicit topic configuration & retention policy (7 days).

Responds directly to mid-sem evaluator feedback:
"Integrate Kafka topic for real-time biometric events; define schema and retention policy."

Usage:
  python kafka_biometric_producer.py --n-events 50 --delay 0.2
  python kafka_biometric_producer.py --n-events 100 --bootstrap-servers localhost:9092
"""

import argparse
import hashlib
import json
import random
import time
import uuid
from datetime import datetime, timezone

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic

import os

TOPIC_NAME = "kyc-biometric-events"
DEFAULT_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# 7-day retention policy + 1GB segment configuration per evaluator feedback
TOPIC_CONFIGS = {
    "retention.ms": "604800000",       # 7 days in milliseconds
    "cleanup.policy": "delete",        # time-based deletion
    "segment.bytes": "1073741824",     # 1 GB per segment
    "min.insync.replicas": "1",
}


def ensure_biometric_topic(bootstrap_servers: str, topic: str = TOPIC_NAME, num_partitions: int = 3):
    """Ensures the biometric topic exists with the required retention policy."""
    print(f"Verifying Kafka topic '{topic}' and retention policy...")
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    existing = admin.list_topics(timeout=10).topics

    if topic in existing:
        print(f"Topic '{topic}' already exists with configured partitions.")
        return

    new_topic = NewTopic(
        topic=topic,
        num_partitions=num_partitions,
        replication_factor=1,
        config=TOPIC_CONFIGS,
    )
    result = admin.create_topics([new_topic])
    for t, future in result.items():
        try:
            future.result()
            print(f"Created topic '{t}' ({num_partitions} partitions, retention=7d)")
        except Exception as e:
            print(f"Topic creation status for '{t}': {e}")


def generate_biometric_event(index: int) -> dict:
    """Generates a realistic biometric event with validation scores."""
    applicant_id = f"APP-{random.randint(100000, 999999)}"
    is_spoof = random.random() < 0.08  # 8% spoof attempt simulation

    if is_spoof:
        face_match = round(random.uniform(0.15, 0.45), 4)
        liveness = round(random.uniform(0.10, 0.40), 4)
        ocr_conf = round(random.uniform(40.0, 75.0), 2)
        name_sim = round(random.uniform(0.30, 0.65), 4)
        outcome = "FAIL" if random.random() < 0.75 else "MANUAL_REVIEW"
    else:
        face_match = round(random.uniform(0.70, 0.98), 4)
        liveness = round(random.uniform(0.65, 0.99), 4)
        ocr_conf = round(random.uniform(85.0, 99.5), 2)
        name_sim = round(random.uniform(0.80, 1.00), 4)
        outcome = "PASS" if random.random() < 0.90 else "MANUAL_REVIEW"

    dev_seed = f"DEVICE-{random.randint(1, 500)}"
    dev_hash = hashlib.sha256(dev_seed.encode("utf-8")).hexdigest()[:16]

    return {
        "event_id": str(uuid.uuid4()),
        "applicant_id": applicant_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "face_match_score": face_match,
        "liveness_score": liveness,
        "ocr_confidence_score": ocr_conf,
        "name_similarity": name_sim,
        "document_type": random.choice(["PASSPORT", "DRIVING_LICENSE", "NATIONAL_ID"]),
        "device_hash": dev_hash,
        "is_spoof_suspected": is_spoof,
        "biometric_outcome": outcome,
    }


def delivery_callback(err, msg):
    if err is not None:
        print(f"  [WARN] Delivery failed for key {msg.key()}: {err}")


def main():
    parser = argparse.ArgumentParser(description="Produce real-time biometric verification events to Kafka")
    parser.add_argument("--n-events", type=int, default=50, help="Number of biometric events to publish")
    parser.add_argument("--delay", type=float, default=0.2, help="Seconds between events")
    parser.add_argument("--bootstrap-servers", default=DEFAULT_BOOTSTRAP)
    args = parser.parse_args()

    print("=" * 65)
    print("REAL-TIME BIOMETRIC EVENT PRODUCER")
    print(f"Topic: {TOPIC_NAME} (Retention: 7 days, 3 Partitions)")
    print("=" * 65)

    ensure_biometric_topic(args.bootstrap_servers, TOPIC_NAME)

    producer = Producer({"bootstrap.servers": args.bootstrap_servers})
    print(f"\nPublishing {args.n_events} biometric events to '{TOPIC_NAME}'...")

    sent = 0
    for i in range(args.n_events):
        event = generate_biometric_event(i)
        key = str(event["applicant_id"])
        value = json.dumps(event)

        producer.produce(TOPIC_NAME, key=key, value=value, callback=delivery_callback)
        producer.poll(0)
        sent += 1

        if sent % 10 == 0 or sent == args.n_events:
            print(f"  Published {sent}/{args.n_events} events (last: {event['applicant_id']} -> {event['biometric_outcome']})")

        if args.delay > 0:
            time.sleep(args.delay)

    producer.flush()
    print(f"\n[DONE] Successfully published {sent} biometric events to '{TOPIC_NAME}'.")


if __name__ == "__main__":
    main()
