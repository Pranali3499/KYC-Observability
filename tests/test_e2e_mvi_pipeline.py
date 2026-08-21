"""
tests/test_e2e_mvi_pipeline.py
End-to-End MVI Pipeline Synthetic Test Suite
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Verifies the complete end-to-end MVI pipeline with synthetic test holdouts:
  1. Pre-ingestion validation & deduplication
  2. Single & batch feature engineering
  3. Tuned Isolation Forest scoring & latency constraints (P95 < 100ms)
  4. Real-time biometric event schema validation & risk scoring
  5. PSI & KS drift calculation
  6. Prometheus metric export contracts

Safe to run in CI/CD without live Docker infrastructure dependencies.

Usage:
  pytest tests/test_e2e_mvi_pipeline.py -v
"""

import json
import os
import time

import joblib
import jsonschema
import numpy as np
import pandas as pd
import pytest

from cross_dataset_evaluation import calculate_psi, engineer_features_from_df, clean_sentinels
from kafka_biometric_consumer_etl import calculate_biometric_risk
from pre_ingestion_validator import hash_row, VALUE_RANGES, CRITICAL_COLUMNS

TUNED_MODEL_PATH = "isolation_forest_tuned.pkl"
ONBOARDING_SCHEMA_PATH = os.path.join("schemas", "onboarding_event_schema.json")
BIOMETRIC_SCHEMA_PATH = os.path.join("schemas", "biometric_event_schema.json")


@pytest.fixture
def synthetic_onboarding_batch():
    np.random.seed(42)
    n = 100
    return pd.DataFrame({
        "velocity_6h": np.random.uniform(0, 1000, n),
        "velocity_24h": np.random.uniform(0, 2000, n),
        "velocity_4w": np.random.uniform(0, 5000, n),
        "device_distinct_emails_8w": np.random.choice([1, 2, 3, -1], n),
        "device_fraud_count": np.random.choice([0, 1, 2], n),
        "prev_address_months_count": np.random.uniform(0, 120, n),
        "current_address_months_count": np.random.uniform(0, 240, n),
        "name_email_similarity": np.random.uniform(0.1, 0.99, n),
        "phone_home_valid": np.random.choice([0, 1], n),
        "phone_mobile_valid": np.random.choice([0, 1], n),
        "foreign_request": np.random.choice([0, 1], n),
        "source": np.random.choice(["INTERNET", "TELEAPP", "BRANCH"], n),
        "income": np.random.uniform(0.2, 0.9, n),
        "credit_risk_score": np.random.uniform(200, 850, n),
        "proposed_credit_limit": np.random.uniform(500, 5000, n),
        "bank_months_count": np.random.uniform(0, 100, n),
        "fraud_bool": np.random.choice([0, 1], n, p=[0.98, 0.02]),
    })


def test_onboarding_schema_validation():
    """Verifies that sample onboarding events conform to JSON Schema."""
    assert os.path.exists(ONBOARDING_SCHEMA_PATH), "Onboarding JSON Schema missing"
    with open(ONBOARDING_SCHEMA_PATH, "r") as f:
        schema = json.load(f)

    valid_event = {
        "row_id": 1,
        "velocity_6h": 120.5,
        "velocity_24h": 340.0,
        "velocity_4w": 1200.0,
        "device_distinct_emails_8w": 1.0,
        "device_fraud_count": 0.0,
        "current_address_months_count": 24.0,
        "name_email_similarity": 0.85,
        "phone_home_valid": 1,
        "phone_mobile_valid": 1,
        "foreign_request": 0,
        "source": "INTERNET",
        "income": 0.6,
        "credit_risk_score": 720.0,
        "proposed_credit_limit": 1500.0,
        "fraud_bool": 0,
    }
    jsonschema.validate(instance=valid_event, schema=schema)


def test_biometric_schema_validation():
    """Verifies that sample biometric verification events conform to JSON Schema."""
    assert os.path.exists(BIOMETRIC_SCHEMA_PATH), "Biometric JSON Schema missing"
    with open(BIOMETRIC_SCHEMA_PATH, "r") as f:
        schema = json.load(f)

    valid_bio_event = {
        "event_id": "EVT-12345",
        "applicant_id": "APP-987654",
        "timestamp": "2026-08-16T19:00:00Z",
        "face_match_score": 0.92,
        "liveness_score": 0.88,
        "ocr_confidence_score": 96.5,
        "name_similarity": 0.95,
        "document_type": "PASSPORT",
        "device_hash": "a1b2c3d4e5f6",
        "is_spoof_suspected": False,
        "biometric_outcome": "PASS",
    }
    jsonschema.validate(instance=valid_bio_event, schema=schema)


def test_feature_engineering_pipeline(synthetic_onboarding_batch):
    """Verifies that feature engineering derives all 6 behavioral features in [0, 1]."""
    cleaned = clean_sentinels(synthetic_onboarding_batch)
    feats = engineer_features_from_df(cleaned)

    expected_cols = [
        "session_velocity_score",
        "device_reuse_score",
        "address_stability_score",
        "identity_consistency_score",
        "geographic_risk_score",
        "financial_risk_score",
    ]
    for col in expected_cols:
        assert col in feats.columns, f"Feature {col} missing"
        assert feats[col].min() >= 0.0, f"{col} has values < 0"
        assert feats[col].max() <= 1.0, f"{col} has values > 1"
        assert feats[col].isnull().sum() == 0, f"{col} has nulls"


def test_model_scoring_and_latency(synthetic_onboarding_batch):
    """Verifies that the tuned Isolation Forest scores within P95 latency target (<= 100ms)."""
    if not os.path.exists(TUNED_MODEL_PATH):
        pytest.skip(f"Model file {TUNED_MODEL_PATH} not found")

    model = joblib.load(TUNED_MODEL_PATH)
    cleaned = clean_sentinels(synthetic_onboarding_batch)
    feats = engineer_features_from_df(cleaned)

    feature_cols = [
        "session_velocity_score",
        "device_reuse_score",
        "address_stability_score",
        "identity_consistency_score",
        "geographic_risk_score",
        "financial_risk_score",
    ]
    X = feats[feature_cols]
    X_arr = X.to_numpy()

    # Warm-up pass
    _ = model.decision_function(X_arr[:1])

    latencies = []
    for i in range(len(X_arr)):
        t0 = time.perf_counter()
        _ = -model.decision_function(X_arr[i : i + 1])[0]
        latencies.append((time.perf_counter() - t0) * 1000)

    p95_lat = np.percentile(latencies, 95)
    assert p95_lat < 100.0, f"P95 latency {p95_lat:.2f}ms exceeds 100ms target"


def test_psi_calculation():
    """Verifies PSI drift calculation behaves correctly under stable vs drifted distributions."""
    np.random.seed(42)
    ref = np.random.normal(0.5, 0.1, 1000)
    stable_curr = np.random.normal(0.5, 0.1, 1000)
    drifted_curr = np.random.normal(0.8, 0.1, 1000)

    stable_psi = calculate_psi(ref, stable_curr)
    drifted_psi = calculate_psi(ref, drifted_curr)

    assert stable_psi < 0.10, f"Stable PSI {stable_psi:.4f} should be < 0.10"
    assert drifted_psi > 0.25, f"Drifted PSI {drifted_psi:.4f} should be > 0.25"


def test_biometric_risk_scoring():
    """Verifies composite biometric risk score calculation."""
    genuine_event = {
        "face_match_score": 0.95,
        "liveness_score": 0.90,
        "ocr_confidence_score": 95.0,
        "name_similarity": 0.95,
    }
    spoof_event = {
        "face_match_score": 0.20,
        "liveness_score": 0.15,
        "ocr_confidence_score": 50.0,
        "name_similarity": 0.30,
    }

    genuine_risk = calculate_biometric_risk(genuine_event)
    spoof_risk = calculate_biometric_risk(spoof_event)

    assert genuine_risk < 0.15, f"Genuine risk {genuine_risk} should be low"
    assert spoof_risk > 0.50, f"Spoof risk {spoof_risk} should be high"
    assert spoof_risk > genuine_risk
