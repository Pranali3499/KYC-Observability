"""
tests/test_alerting.py
Stage 8 -- Unit tests for alerting.py

Tests build_alert_message() (pure, no network) directly, and
send_drift_alert()'s graceful-degradation path when no webhook is
configured (also no network -- it only prints in that case).
"""

import os

import pandas as pd
import pytest

from alerting import build_alert_message, send_drift_alert, WEBHOOK_URL_ENV_VAR


def _report(rows):
    return pd.DataFrame(rows)


class TestBuildAlertMessage:
    def test_all_ok_produces_empty_message(self):
        report = _report([
            {"feature": "session_velocity_score", "psi": 0.01, "ks_pvalue": 0.8,
             "status": "OK", "sample_size_warning": False},
        ])
        assert build_alert_message(report, 500, False) == ""

    def test_alert_row_included_ok_row_excluded(self):
        report = _report([
            {"feature": "device_reuse_score", "psi": 0.97, "ks_pvalue": 0.0,
             "status": "ALERT", "sample_size_warning": False},
            {"feature": "session_velocity_score", "psi": 0.01, "ks_pvalue": 0.8,
             "status": "OK", "sample_size_warning": False},
        ])
        msg = build_alert_message(report, 500, False)
        assert "device_reuse_score" in msg
        assert "session_velocity_score" not in msg

    def test_warning_row_also_included(self):
        report = _report([
            {"feature": "financial_risk_score", "psi": 0.15, "ks_pvalue": 0.3,
             "status": "WARNING", "sample_size_warning": False},
        ])
        msg = build_alert_message(report, 500, False)
        assert "financial_risk_score" in msg
        assert "WARNING" in msg

    def test_synthetic_data_flagged_in_message(self):
        report = _report([
            {"feature": "device_reuse_score", "psi": 0.97, "ks_pvalue": 0.0,
             "status": "ALERT", "sample_size_warning": False},
        ])
        msg = build_alert_message(report, 20000, True)
        assert "SYNTHETIC" in msg

    def test_low_confidence_caveat_shown_when_flagged(self):
        report = _report([
            {"feature": "device_reuse_score", "psi": 0.97, "ks_pvalue": 0.0,
             "status": "ALERT", "sample_size_warning": True},
        ])
        msg = build_alert_message(report, 80, False)
        assert "low confidence" in msg


class TestSendDriftAlertWithoutWebhook:
    def setup_method(self):
        # Ensure no webhook URL leaks in from the test environment
        os.environ.pop(WEBHOOK_URL_ENV_VAR, None)

    def test_returns_false_when_nothing_flagged(self):
        report = _report([
            {"feature": "session_velocity_score", "psi": 0.01, "ks_pvalue": 0.8,
             "status": "OK", "sample_size_warning": False},
        ])
        assert send_drift_alert(report, 500, False) is False

    def test_returns_true_and_degrades_gracefully_when_flagged(self, capsys):
        report = _report([
            {"feature": "device_reuse_score", "psi": 0.97, "ks_pvalue": 0.0,
             "status": "ALERT", "sample_size_warning": False},
        ])
        result = send_drift_alert(report, 500, False)
        assert result is True
        captured = capsys.readouterr()
        assert "No DRIFT_ALERT_WEBHOOK_URL configured" in captured.out
