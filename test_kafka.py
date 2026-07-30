"""
test_kafka.py

Simple script to verify that Python can connect to your Kafka broker
and retrieve broker metadata.
"""

from confluent_kafka.admin import AdminClient
from confluent_kafka import KafkaException

BOOTSTRAP_SERVERS = "localhost:9092"

print("=" * 60)
print("Testing Kafka Connection")
print("=" * 60)
print(f"Bootstrap Server: {BOOTSTRAP_SERVERS}\n")

try:
    admin = AdminClient({
        "bootstrap.servers": BOOTSTRAP_SERVERS
    })

    print("Connecting to Kafka broker...")

    metadata = admin.list_topics(timeout=10)

    print("\n✅ Connection Successful!\n")

    print("Broker(s):")
    for broker_id, broker in metadata.brokers.items():
        print(f"  Broker ID : {broker_id}")
        print(f"  Host      : {broker.host}")
        print(f"  Port      : {broker.port}")
        print()

    print("Available Topics:")
    for topic in metadata.topics.keys():
        print(f"  - {topic}")

    print("\nKafka is working correctly.")

except KafkaException as e:
    print("\n❌ KafkaException:")
    print(e)

except Exception as e:
    print("\n❌ Unexpected Error:")
    print(type(e).__name__)
    print(e)

print("\nDone.")
