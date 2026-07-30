"""
tests/test_feature_engineering.py
Stage 8 -- Unit tests for feature_engineering.py

Scope note: these test the PURE functions only (_minmax, clean_sentinels) --
no PostgreSQL connection is opened, no live data is read. That's a
deliberate PoC-scope choice: importing feature_engineering.py at
collection time is safe (it doesn't call get_engine() at module level,
only inside main()), so these run in CI with no Docker services needed.
"""

import numpy as np
import pandas as pd
import pytest

from feature_engineering import _minmax, clean_sentinels, SENTINEL_COLS


class TestMinMax:
    def test_scales_to_zero_one_range(self):
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        result = _minmax(s)
        assert result.min() == pytest.approx(0.0)
        assert result.max() == pytest.approx(1.0)

    def test_preserves_relative_order(self):
        s = pd.Series([5.0, 1.0, 3.0])
        result = _minmax(s)
        assert result.iloc[1] < result.iloc[2] < result.iloc[0]

    def test_constant_series_returns_zeros(self):
        # hi - lo == 0 case -- must not divide by zero
        s = pd.Series([7.0, 7.0, 7.0])
        result = _minmax(s)
        assert (result == 0).all()

    def test_negative_values_handled(self):
        s = pd.Series([-10.0, 0.0, 10.0])
        result = _minmax(s)
        assert result.min() == pytest.approx(0.0)
        assert result.max() == pytest.approx(1.0)
        assert result.iloc[1] == pytest.approx(0.5)


class TestCleanSentinels:
    def test_replaces_negative_one_with_median(self):
        # BAF's missing-value sentinel is -1. clean_sentinels should
        # replace it with the median of the genuine (non-sentinel) values.
        df = pd.DataFrame({
            "current_address_months_count": [10.0, 20.0, 30.0, -1.0, -1.0],
        })
        result = clean_sentinels(df.copy())
        assert -1.0 not in result["current_address_months_count"].values
        # median of [10, 20, 30] == 20
        assert (result.loc[3:4, "current_address_months_count"] == 20.0).all()

    def test_leaves_genuine_values_untouched(self):
        df = pd.DataFrame({
            "bank_months_count": [5.0, 15.0, 25.0],
        })
        result = clean_sentinels(df.copy())
        pd.testing.assert_series_equal(
            result["bank_months_count"], df["bank_months_count"], check_dtype=False
        )

    def test_no_sentinels_present_is_a_noop(self):
        df = pd.DataFrame({"session_length_in_minutes": [1.0, 2.0, 3.0]})
        result = clean_sentinels(df.copy())
        pd.testing.assert_series_equal(
            result["session_length_in_minutes"], df["session_length_in_minutes"]
        )

    def test_column_not_present_is_skipped_without_error(self):
        # SENTINEL_COLS includes columns that may not exist in every
        # slice of data passed in -- must not raise a KeyError.
        df = pd.DataFrame({"unrelated_column": [1, 2, 3]})
        result = clean_sentinels(df.copy())
        assert "unrelated_column" in result.columns

    def test_sentinel_cols_list_is_not_empty(self):
        # Sanity check on the constant itself -- if someone empties this
        # list by accident, every sentinel-cleaning test above would
        # trivially pass without testing anything.
        assert len(SENTINEL_COLS) > 0
