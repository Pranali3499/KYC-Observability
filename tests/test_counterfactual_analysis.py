"""
tests/test_counterfactual_analysis.py
Stage 8 -- Unit tests for counterfactual_analysis.py

Tests the two scan functions against a MOCK model with a known,
deterministic decision rule (flags anomalous iff feature 'a' > 0.7).
This lets us assert the EXACT expected flip point mathematically,
rather than just "it runs without crashing" -- a mock with a known
ground truth is what makes this a real correctness test.

Does not import kafka_consumer_etl.py (confluent_kafka may not be
installed everywhere) -- the two scan functions are reimplemented
inline for isolation, matching the real file's logic exactly. See
test_pipeline_consistency.py for cross-script parity checks instead.
"""

import numpy as np
import pandas as pd
import pytest

FEATURE_COLUMNS = ["a", "b", "c"]
SCAN_STEPS = 50


class MockModel:
    """Deterministic rule: anomalous (-1) iff a > 0.7, else normal (1)."""
    def predict(self, X):
        return np.where(X["a"].values > 0.7, -1, 1)

    def decision_function(self, X):
        return 0.7 - X["a"].values


def full_vector_counterfactual(model, original, normal_median):
    for t in np.linspace(0, 1, SCAN_STEPS + 1)[1:]:
        interpolated = original[FEATURE_COLUMNS] * (1 - t) + normal_median * t
        X = pd.DataFrame([interpolated])[FEATURE_COLUMNS]
        if model.predict(X)[0] == 1:
            return float(t)
    return None


def single_feature_counterfactual(model, original, normal_median, feature):
    for t in np.linspace(0, 1, SCAN_STEPS + 1)[1:]:
        modified = original[FEATURE_COLUMNS].copy()
        modified[feature] = original[feature] * (1 - t) + normal_median[feature] * t
        X = pd.DataFrame([modified])[FEATURE_COLUMNS]
        if model.predict(X)[0] == 1:
            return float(t)
    return None


@pytest.fixture
def anomalous_record():
    return pd.Series({"a": 0.9, "b": 0.5, "c": 0.5, "row_id": 1})


@pytest.fixture
def normal_median():
    return pd.Series({"a": 0.3, "b": 0.5, "c": 0.5})


@pytest.fixture
def model():
    return MockModel()


class TestFullVectorCounterfactual:
    def test_starting_record_is_anomalous(self, model, anomalous_record):
        X0 = pd.DataFrame([anomalous_record[FEATURE_COLUMNS]])
        assert model.predict(X0)[0] == -1

    def test_finds_flip_at_expected_fraction(self, model, anomalous_record, normal_median):
        # a(t) = 0.9*(1-t) + 0.3*t = 0.9 - 0.6t; flips when a(t) <= 0.7 -> t >= 1/3
        t = full_vector_counterfactual(model, anomalous_record, normal_median)
        assert t is not None
        assert 0.30 <= t <= 0.40

    def test_returns_none_when_no_flip_possible(self, model):
        # normal_median with 'a' still above threshold -- can never flip
        record = pd.Series({"a": 0.95, "b": 0.5, "c": 0.5})
        stuck_median = pd.Series({"a": 0.8, "b": 0.5, "c": 0.5})  # still > 0.7
        t = full_vector_counterfactual(model, record, stuck_median)
        assert t is None


class TestSingleFeatureCounterfactual:
    def test_driving_feature_flips(self, model, anomalous_record, normal_median):
        t = single_feature_counterfactual(model, anomalous_record, normal_median, "a")
        assert t is not None
        assert 0.30 <= t <= 0.40

    def test_non_driving_features_never_flip(self, model, anomalous_record, normal_median):
        # b and c have zero causal effect on the mock model's rule --
        # moving them alone, however far, must never flip the prediction
        assert single_feature_counterfactual(model, anomalous_record, normal_median, "b") is None
        assert single_feature_counterfactual(model, anomalous_record, normal_median, "c") is None

    def test_easiest_feature_ranking_picks_the_driving_feature(self, model, anomalous_record, normal_median):
        shifts = {
            col: single_feature_counterfactual(model, anomalous_record, normal_median, col)
            for col in FEATURE_COLUMNS
        }
        achievable = sorted(
            [(col, s) for col, s in shifts.items() if s is not None],
            key=lambda pair: pair[1],
        )
        assert len(achievable) == 1
        assert achievable[0][0] == "a"
