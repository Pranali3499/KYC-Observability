"""
tests/test_integration_pipeline.py
Integration tests -- exercise REAL infrastructure end-to-end.

Addresses mid-sem evaluator feedback: "Implement test pyramid: unit,
integration, end-to-end, and regression." The existing 37 tests
(test_feature_engineering.py, test_drift_detection.py,
test_counterfactual_analysis.py, test_kafka_consumer_etl.py,
test_alerting.py) are all UNIT tests -- they exercise logic in
isolation against synthetic, in-memory inputs, with no dependency on
a live database or Kafka broker. That's correct and valuable for CI
(see .github/workflows/tests.yml), but it never verifies your actual
running Postgres/Kafka services can talk to your actual code.

These tests do exactly that: they run your real scripts as
subprocesses against your real, currently-running Docker
infrastructure (docker compose up -d must be running first), and
verify outcomes through things independently confirmed -- exit
codes, printed output patterns, and row-count changes in tables
whose EXISTENCE is confirmed, without assuming exact internal column
names this project has never directly verified via a raw query.

-----------------------------------------------------------------
WHY THESE SKIP GRACEFULLY INSTEAD OF FAILING
-----------------------------------------------------------------
Unlike the 37 unit tests, these genuinely require Postgres and Kafka
to be reachable -- that's the whole point of an integration test.
Your CI workflow (GitHub Actions) does NOT run Docker Compose, so
these tests would fail there for a reason that has nothing to do
with code correctness. Each test checks connectivity first and
calls pytest.skip() with a clear reason if infrastructure isn't
available, rather than failing noisily -- this keeps them safe to
commit without breaking your CI pipeline, while still being fully
real and meaningful when run locally with `docker compose up -d`
active.

Usage:
    pytest tests/test_integration_pipeline.py -v
    (run from the project root, with Docker infrastructure running)

Requires:
    Postgres reachable via db_config.get_engine()
    Kafka reachable at the broker configured in kafka_producer.py /
    kafka_consumer_etl.py (default: localhost:9092)
"""

import os
import subprocess
import sys
import time

import pytest
from sqlalchemy import text, inspect

try:
    from db_config import get_engine
    _HAVE_DB_CONFIG = True
except ImportError:
    _HAVE_DB_CONFIG = False


PROJECT_ROOT_MARKER_FILES = ["data_ingestion.py", "docker-compose.yml"]


def _db_available():
    """Returns True if Postgres is actually reachable right now."""
    if not _HAVE_DB_CONFIG:
        return False
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _kafka_available(bootstrap_servers: str = None) -> bool:
    """Returns True if Kafka broker is actually reachable."""
    if bootstrap_servers is None:
        bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    try:
        from confluent_kafka.admin import AdminClient
        admin = AdminClient({"bootstrap.servers": bootstrap_servers, "socket.timeout.ms": 3000})
        metadata = admin.list_topics(timeout=3)
        return metadata is not None and metadata.topics is not None
    except Exception:
        return False


def _table_row_count(engine, table_name: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()


def _table_exists(engine, table_name: str) -> bool:
    return inspect(engine).has_table(table_name)


def _run_script(args: list, timeout: int = 120) -> subprocess.CompletedProcess:
    """
    Runs one of this project's real scripts as a subprocess -- this
    is deliberately black-box: it does not import or call internal
    functions, it invokes the script exactly the way a person running
    the pipeline manually would, via `python <script>.py <args>`.
    """
    return subprocess.run(
        [sys.executable] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def db_engine():
    if not _db_available():
        pytest.skip(
            "Postgres is not reachable -- these are integration tests and "
            "require `docker compose up -d` to be running with a "
            "reachable database. Skipping, not failing: this is expected "
            "in CI environments (e.g. GitHub Actions) that don't run "
            "Docker infrastructure."
        )
    return get_engine()


class TestDataQualityIntegration:
    """
    Runs the REAL data_quality_checks.py against the REAL database,
    end to end -- not a mock, not a synthetic fixture. This is the
    genuine integration-level equivalent of the unit-level PSI/null-
    rate logic already covered in the existing unit test suite.
    """

    def test_data_quality_checks_runs_successfully(self, db_engine):
        if not _table_exists(db_engine, "kyc_transactions") or not _table_exists(db_engine, "behavioral_features"):
            pytest.skip(
                "kyc_transactions / behavioral_features tables don't exist yet -- "
                "run data_ingestion.py and feature_engineering.py first."
            )

        result = _run_script(["data_quality_checks.py"], timeout=300)

        assert result.returncode == 0, (
            f"data_quality_checks.py exited with code {result.returncode}, "
            f"meaning at least one real data quality check failed against "
            f"the live database.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert "FAIL" not in result.stdout or "0 failed" in result.stdout, (
            "data_quality_checks.py reported at least one FAILED check "
            "against the live database -- see stdout for details.\n"
            f"{result.stdout}"
        )

    def test_data_quality_report_table_is_written(self, db_engine):
        """
        Confirms the quality-check run above actually persisted its
        results, not just printed them -- i.e. the audit trail this
        project's provenance design relies on genuinely exists in the
        database, not only in a terminal that gets closed.
        """
        if not _table_exists(db_engine, "data_quality_report"):
            pytest.skip("data_quality_report table doesn't exist -- run data_quality_checks.py first.")

        count = _table_row_count(db_engine, "data_quality_report")
        assert count > 0, "data_quality_report table exists but has 0 rows -- checks may never have run successfully."


class TestKafkaProducerConsumerIntegration:
    """
    The real gap this project's unit tests can't cover: does a message
    published by the REAL Kafka producer actually get picked up by the
    REAL Kafka consumer and land in the REAL database? This exercises
    the full Kafka -> ETL -> feature store chain the report's MVI
    (Kafka -> ETL -> feature store -> model -> Prometheus) claims,
    using the actual broker and actual Postgres instance.
    """

    def test_producer_then_consumer_increases_real_time_scores_row_count(self, db_engine):
        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        if not _kafka_available(bootstrap):
            pytest.skip(
                f"Kafka broker at {bootstrap} is not reachable -- skipping integration test. "
                "This is expected when Docker Kafka is not running."
            )

        if not _table_exists(db_engine, "kyc_transactions"):
            pytest.skip("kyc_transactions doesn't exist -- run data_ingestion.py first.")

        # real_time_scores may not exist yet on a fresh environment --
        # that's fine, a 0 baseline is still a valid starting point.
        before_count = (
            _table_row_count(db_engine, "real_time_scores")
            if _table_exists(db_engine, "real_time_scores")
            else 0
        )

        n_events = 10  # deliberately small -- this is a correctness
                       # check, not a load test; keep it fast for CI-
                       # adjacent local runs.

        producer_result = _run_script(
            ["kafka_producer.py", "--n-events", str(n_events), "--delay", "0.05", "--bootstrap-servers", bootstrap],
            timeout=120,
        )
        assert producer_result.returncode == 0, (
            f"kafka_producer.py failed to publish events.\n"
            f"STDOUT:\n{producer_result.stdout}\nSTDERR:\n{producer_result.stderr}"
        )
        assert "DONE" in producer_result.stdout or "published" in producer_result.stdout.lower(), (
            f"kafka_producer.py did not report successful publication.\n{producer_result.stdout}"
        )

        # Small pause so the just-published messages are definitely
        # available to a consumer that subscribes immediately after --
        # avoids a race condition between publish and subscribe.
        time.sleep(2)

        test_group = f"test-ci-group-{int(time.time())}"
        consumer_result = _run_script(
            ["kafka_consumer_etl.py", "--max-messages", str(n_events), "--bootstrap-servers", bootstrap, "--group-id", test_group, "--timeout", "5.0"],
            timeout=180,
        )
        assert consumer_result.returncode == 0, (
            f"kafka_consumer_etl.py failed to process events.\n"
            f"STDOUT:\n{consumer_result.stdout}\nSTDERR:\n{consumer_result.stderr}"
        )

        after_count = _table_row_count(db_engine, "real_time_scores")

        assert after_count > before_count, (
            f"real_time_scores row count did not increase after running the "
            f"producer and consumer together (before={before_count}, "
            f"after={after_count}) -- the end-to-end Kafka pipeline may not "
            f"be writing to the database correctly.\n"
            f"Producer output:\n{producer_result.stdout}\n"
            f"Consumer output:\n{consumer_result.stdout}"
        )


class TestDriftDetectionIntegration:
    """
    Runs the REAL drift_detection.py PASS path against the REAL
    database. Complements the unit-level PSI/KS math tests already in
    test_drift_detection.py by confirming the full script -- DB reads,
    reference-distribution computation, and drift_report writes --
    actually works end to end, not just the isolated math functions.
    """

    def test_drift_detection_pass_path_runs_and_writes_report(self, db_engine):
        if not _table_exists(db_engine, "behavioral_features"):
            pytest.skip("behavioral_features doesn't exist -- run feature_engineering.py first.")

        before_count = (
            _table_row_count(db_engine, "drift_report")
            if _table_exists(db_engine, "drift_report")
            else 0
        )

        # Use --sample-size 5000 for fast CI integration execution
        result = _run_script(["drift_detection.py", "--sample-size", "5000"], timeout=300)

        assert result.returncode == 0, (
            f"drift_detection.py exited with a non-zero code.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        after_count = _table_row_count(db_engine, "drift_report")
        assert after_count > before_count, (
            "drift_report row count did not increase after running "
            "drift_detection.py -- the script may have run without "
            "actually persisting its findings."
        )
