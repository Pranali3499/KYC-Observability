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
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
from sqlalchemy import create_engine

TOPIC_NAME = "kyc-onboarding-events"
SOURCE_TABLE = "kyc_transactions"
DEFAULT_BOOTSTRAP = "localhost:9092"
DEFAULT_DB_URL = "postgresql://kyc_user:kyc_pass@localhost:5432/kyc_db"


def ensure_topic_exists(bootstrap_servers: str, topic: str, num_partitions: int = 3):
    admin = KafkaAdminClient(bootstrap_servers=bootstrap_servers)
    try:
        admin.create_topics([NewTopic(name=topic, num_partitions=num_partitions, replication_factor=1)])
        print(f"Created topic '{topic}' ({num_partitions} partitions)")
    except TopicAlreadyExistsError:
        print(f"Topic '{topic}' already exists -- using it")
    finally:
        admin.close()


def load_sample_events(db_url: str, n_events: int) -> list[dict]:
    print(f"Sampling {n_events} rows from '{SOURCE_TABLE}' to simulate as live events...")
    engine = create_engine(db_url)
    # random sample, not the first N rows -- more representative of a
    # real stream than always replaying the same head of the table
    df = pd.read_sql(
        f"SELECT * FROM {SOURCE_TABLE} ORDER BY RANDOM() LIMIT {n_events}", engine
    )
    print(f"Loaded {len(df)} sample events")
    return df.to_dict(orient="records")


def main():
    parser = argparse.ArgumentParser(description="Stage 3: Kafka producer -- simulated onboarding events")
    parser.add_argument("--n-events", type=int, default=100, help="Number of events to publish")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds to wait between events (0 = as fast as possible)")
    parser.add_argument("--bootstrap-servers", default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--db-url", default=DEFAULT_DB_URL)
    args = parser.parse_args()

    print("=" * 65)
    print("STAGE 3 -- Kafka Producer (Simulated Onboarding Events)")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    ensure_topic_exists(args.bootstrap_servers, TOPIC_NAME)

    events = load_sample_events(args.db_url, args.n_events)

    producer = KafkaProducer(
        bootstrap_servers=args.bootstrap_servers,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        key_serializer=lambda k: str(k).encode("utf-8"),
    )

    print(f"\nPublishing {len(events)} events to topic '{TOPIC_NAME}' "
          f"(delay={args.delay}s between events)...")

    sent = 0
    for event in events:
        # row_id as the message key -- lets Kafka partition consistently
        # by applicant, and gives the consumer a natural dedup key
        key = event.get("row_id", sent)
        producer.send(TOPIC_NAME, key=key, value=event)
        sent += 1
        if sent % 10 == 0 or sent == len(events):
            print(f"  Sent {sent}/{len(events)} events")
        if args.delay > 0:
            time.sleep(args.delay)

    producer.flush()
    producer.close()

    print(f"\n[producer] DONE -- {sent} events published to '{TOPIC_NAME}'")


if __name__ == "__main__":
    main()
