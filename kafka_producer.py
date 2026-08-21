"""
kafka_producer.py
Stage 3 -- MVI Real-Time Skeleton: Producer
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Simulates a live stream of onboarding applications by sampling rows
from kyc_transactions (your already-ingested BAF dataset) and
publishing them, one at a time, to a Kafka topic. This stands in for
what would be real-time onboarding events from a production KYC
system -- the consumer (kafka_consumer_etl.py) doesn't know or care
whether the event came from a live application or this simulator.

Creates the topic on first run if it doesn't already exist.

Usage:
    python kafka_producer.py --n-events 100 --delay 0.5
    python kafka_producer.py --n-events 1000 --delay 0 --bootstrap-servers localhost:9092

Requires:
    pip install kafka-python
"""

import argparse
import json
import time

import pandas as pd
import os
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic
from sqlalchemy import create_engine
from db_config import get_engine

TOPIC_NAME = "kyc-onboarding-events"
SOURCE_TABLE = "kyc_transactions"
DEFAULT_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def ensure_topic_exists(bootstrap_servers: str, topic: str, num_partitions: int = 3):
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    existing = admin.list_topics(timeout=10).topics
    if topic in existing:
        print(f"Topic '{topic}' already exists -- using it")
        return

    new_topic = NewTopic(topic, num_partitions=num_partitions, replication_factor=1)
    result = admin.create_topics([new_topic])
    for t, future in result.items():
        try:
            future.result()  # raises if creation failed
            print(f"Created topic '{t}' ({num_partitions} partitions)")
        except Exception as e:
            print(f"Topic creation for '{t}' failed (may already exist): {e}")


def load_sample_events(n_events: int, db_url: str = None) -> list[dict]:
    print(f"Sampling {n_events} rows from '{SOURCE_TABLE}' to simulate as live events...")
    if db_url:
        engine = create_engine(db_url)
    else:
        engine = get_engine()
    df = pd.read_sql(f"SELECT * FROM {SOURCE_TABLE} LIMIT {n_events}", engine)
    print(f"Loaded {len(df)} sample events")
    return df.to_dict(orient="records")


def delivery_callback(err, msg):
    if err is not None:
        print(f"  [WARN] Delivery failed for key {msg.key()}: {err}")


def main():
    parser = argparse.ArgumentParser(description="Stage 3: Kafka producer -- simulated onboarding events")
    parser.add_argument("--n-events", type=int, default=100, help="Number of events to publish")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds to wait between events (0 = as fast as possible)")
    parser.add_argument("--bootstrap-servers", default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--db-url", default=None, help="Optional DB URL (defaults to db_config settings)")
    args = parser.parse_args()

    print("=" * 65)
    print("STAGE 3 -- Kafka Producer (Simulated Onboarding Events)")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    ensure_topic_exists(args.bootstrap_servers, TOPIC_NAME)

    events = load_sample_events(args.n_events, args.db_url)

    producer = Producer({"bootstrap.servers": args.bootstrap_servers})

    print(f"\nPublishing {len(events)} events to topic '{TOPIC_NAME}' "
          f"(delay={args.delay}s between events)...")

    sent = 0
    for event in events:
        # row_id as the message key -- lets Kafka partition consistently
        # by applicant, and gives the consumer a natural dedup key
        key = str(event.get("row_id", sent))
        value = json.dumps(event, default=str)
        producer.produce(TOPIC_NAME, key=key, value=value, callback=delivery_callback)
        producer.poll(0)  # trigger delivery callbacks without blocking
        sent += 1
        if sent % 10 == 0 or sent == len(events):
            print(f"  Sent {sent}/{len(events)} events")
        if args.delay > 0:
            time.sleep(args.delay)

    producer.flush()

    print(f"\n[producer] DONE -- {sent} events published to '{TOPIC_NAME}'")


if __name__ == "__main__":
    main()
