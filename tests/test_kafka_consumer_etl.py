"""
test_kafka_consumer_etl.py
Stage 8 -- Unit tests for kafka_consumer_etl.py

Tests engineer_features_single() in isolation, with a fixed ranges
dict (no live DB query needed) -- this is the function most likely
to silently break in a way that produces plausible-looking-but-wrong
scores, which is exactly what happened during development (see the
dev log's "everything scored as anomalous" bug from mis-scaled fixed
ranges). These tests exist specifically to catch that class of
regression automatically.

Run with:
    pytest tests/test_kafka_consumer_etl.py -v
"""

import pytest

from kafka_consumer_etl import engineer_features_single, FEATURE_COLUMNS

# A representative ranges dict, structurally identical to what
# compute_feature_ranges() would return from real training data.
SAMPLE_RANGES = {
    "session_velocity": (1000.0, 12000.0),
    "device_reuse": (-1.0, 2.0),
    "address_stability": (0.0, 400.0),
    "identity_consistency": (0.0, 3.0),
    "geographic_risk": (0.0, 2.0),
    "financial_risk": (50.0, 20000.0),
}


class TestEngineerFeaturesSingle:
    def test_returns_all_expected_feature_columns(self):
        event = {
            "velocity_6h": 100, "velocity_24h": 200, "velocity_4w": 300,
            "device_distinct_emails_8w": 1, "device_fraud_count": 0,
            "current_address_months_count": 24,
            "name_email_similarity": 0.5, "phone_home_valid": 1, "phone_mobile_valid": 1,
            "foreign_request": 0, "source": "INTERNET",
            "credit_risk_score": 100, "proposed_credit_limit": 500, "income": 0.5,
        }
        features = engineer_features_single(event, SAMPLE_RANGES)
        assert set(features.keys()) == set(FEATURE_COLUMNS)

    def test_all_features_within_zero_one_range(self):
        """
        The core regression test: every output feature must land in
        [0, 1] regardless of input. This is what silently broke when
        the normalization ranges were mis-calibrated in early
        development, producing scores far outside [0, 1] that fed
        garbage into the model.
        """
        event = {
            "velocity_6h": 5000, "velocity_24h": 8000, "velocity_4w": 15000,
            "device_distinct_emails_8w": 3, "device_fraud_count": 2,
            "current_address_months_count": 500,  # deliberately out-of-range high
            "name_email_similarity": 1.0, "phone_home_valid": 1, "phone_mobile_valid": 1,
            "foreign_request": 1, "source": "TELEAPP",
            "credit_risk_score": 9999, "proposed_credit_limit": 50000, "income": 0.01,
        }
        features = engineer_features_single(event, SAMPLE_RANGES)
        for name, value in features.items():
            assert 0.0 <= value <= 1.0, f"{name}={value} is outside [0, 1]"

    def test_missing_fields_default_safely(self):
        """An event missing most fields (e.g. a malformed Kafka message)
        should not crash -- should degrade to safe defaults."""
        event = {}
        features = engineer_features_single(event, SAMPLE_RANGES)
        assert set(features.keys()) == set(FEATURE_COLUMNS)
        for value in features.values():
            assert 0.0 <= value <= 1.0

    def test_non_numeric_field_does_not_crash(self):
        """Malformed data (e.g. a string where a number is expected)
        should fall back to a default rather than raising."""
        event = {
            "velocity_6h": "not_a_number", "income": None, "credit_risk_score": "NaN",
        }
        features = engineer_features_single(event, SAMPLE_RANGES)
        assert set(features.keys()) == set(FEATURE_COLUMNS)

    def test_zero_income_does_not_divide_by_zero(self):
        """income=0 in financial_risk_score's denominator must not raise
        ZeroDivisionError -- caught by the safe_float default fallback."""
        event = {"income": 0, "proposed_credit_limit": 100, "credit_risk_score": 50}
        features = engineer_features_single(event, SAMPLE_RANGES)
        assert 0.0 <= features["financial_risk_score"] <= 1.0

    def test_telapp_source_increases_geographic_risk(self):
        """Sanity check that the TELEAPP source flag actually affects
        the geographic_risk_score calculation as intended."""
        base_event = {"foreign_request": 0, "source": "INTERNET"}
        teleapp_event = {"foreign_request": 0, "source": "TELEAPP"}

        base_features = engineer_features_single(base_event, SAMPLE_RANGES)
        teleapp_features = engineer_features_single(teleapp_event, SAMPLE_RANGES)

        assert teleapp_features["geographic_risk_score"] >= base_features["geographic_risk_score"]
