"""
api.py
Stage 6 -- ML Scoring API (FastAPI)
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Exposes the tuned Isolation Forest model over HTTP for synchronous,
on-demand scoring -- a second serving path alongside the Kafka
streaming consumer (kafka_consumer_etl.py), for callers that want an
immediate request/response instead of publishing to a topic.

DELIBERATE DESIGN CHOICE: this module does NOT re-implement feature
engineering or scoring logic. It imports engineer_features_single(),
compute_feature_ranges(), load_model(), write_score(), and
FEATURE_COLUMNS directly from kafka_consumer_etl.py. This project
already found and fixed three real bugs (Stage 6) caused by the same
logic being re-expressed slightly differently across scripts -- adding
a third independent implementation here would risk a fourth. One
source of truth for "how a raw event becomes a score" is now
kafka_consumer_etl.py; this API is a thin HTTP wrapper around it.

Usage:
    uvicorn api:app --reload --port 8001

Then:
    curl -X POST http://localhost:8001/score -H "Content-Type: application/json" -d "{...}"

Requires:
    pip install fastapi uvicorn
"""

import time
from contextlib import asynccontextmanager
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from db_config import get_engine
from kafka_consumer_etl import (
    load_model,
    compute_feature_ranges,
    engineer_features_single,
    write_score,
    FEATURE_COLUMNS,
)

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Loads the model, DB engine, and feature-normalization ranges ONCE
    at startup -- same pattern as kafka_consumer_etl.py's main(), so
    every request doesn't re-query kyc_transactions for ranges or
    re-load the 640MB model file from disk.
    """
    print("Starting up: loading model, DB engine, and feature ranges...")
    _state["engine"] = get_engine()
    _state["model"] = load_model()
    _state["ranges"] = compute_feature_ranges(_state["engine"])
    print("Startup complete -- ready to score requests.")
    yield
    _state.clear()
    print("Shutdown complete.")


app = FastAPI(
    title="KYC Behavioral Observability -- Scoring API",
    description="Synchronous scoring endpoint for the Behavioral Observability Framework. "
                "Uses the same tuned Isolation Forest and feature engineering as the Kafka streaming path.",
    version="1.0.0",
    lifespan=lifespan,
)


class OnboardingEvent(BaseModel):
    """
    Raw onboarding fields -- same shape as a kafka-onboarding-events
    message (see kafka_producer.py), not pre-engineered features. The
    API engineers features itself via engineer_features_single(), so
    callers submit the same raw data the batch/streaming pipelines do.
    """
    row_id: Optional[int] = None
    velocity_6h: Optional[float] = 0.0
    velocity_24h: Optional[float] = 0.0
    velocity_4w: Optional[float] = 0.0
    device_distinct_emails_8w: Optional[float] = 0.0
    device_fraud_count: Optional[float] = 0.0
    current_address_months_count: Optional[float] = 0.0
    name_email_similarity: Optional[float] = 0.0
    phone_home_valid: Optional[float] = 0.0
    phone_mobile_valid: Optional[float] = 0.0
    foreign_request: Optional[float] = 0.0
    source: Optional[str] = None
    credit_risk_score: Optional[float] = 0.0
    proposed_credit_limit: Optional[float] = 0.0
    income: Optional[float] = 1.0
    fraud_bool: Optional[int] = None


class ScoreResponse(BaseModel):
    row_id: Optional[int]
    anomaly_score: float
    is_anomaly: bool
    features: dict
    latency_ms: float


@app.get("/health")
def health():
    """Liveness/readiness probe -- confirms the model actually loaded, not just that the process is up."""
    return {
        "status": "ok" if "model" in _state else "starting",
        "model_loaded": "model" in _state,
    }


@app.post("/score", response_model=ScoreResponse)
def score(event: OnboardingEvent):
    if "model" not in _state:
        raise HTTPException(status_code=503, detail="Model not loaded yet -- try again shortly.")

    start = time.perf_counter()
    event_dict = event.model_dump()

    try:
        features = engineer_features_single(event_dict, _state["ranges"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Feature engineering failed: {e}")

    X = pd.DataFrame([features])[FEATURE_COLUMNS]
    anomaly_score = float(-_state["model"].decision_function(X)[0])
    is_anomaly = bool(_state["model"].predict(X)[0] == -1)
    latency_ms = (time.perf_counter() - start) * 1000

    # Written to the SAME real_time_scores table the Kafka consumer
    # writes to -- both serving paths feed one table, so
    # drift_detection.py sees traffic from either source identically.
    write_score(
        _state["engine"], event.row_id, features, anomaly_score,
        is_anomaly, event.fraud_bool, latency_ms,
    )

    return ScoreResponse(
        row_id=event.row_id,
        anomaly_score=anomaly_score,
        is_anomaly=is_anomaly,
        features=features,
        latency_ms=round(latency_ms, 2),
    )


@app.get("/")
def root():
    return {
        "service": "KYC Behavioral Observability -- Scoring API",
        "docs": "/docs",
        "health": "/health",
    }
