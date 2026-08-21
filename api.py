"""
api.py
Stage 6 -- ML Scoring API (FastAPI) with Prometheus Observability
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Exposes the tuned Isolation Forest model over HTTP for synchronous,
on-demand scoring -- a second serving path alongside the Kafka
streaming consumer (kafka_consumer_etl.py), for callers that want an
immediate request/response instead of publishing to a topic.

DELIBERATE DESIGN CHOICE: this module does NOT re-implement feature
engineering or scoring logic. It imports engineer_features_single(),
compute_feature_ranges(), load_model(), write_score(), and
FEATURE_COLUMNS directly from kafka_consumer_etl.py.

Observability instrumentation (Prometheus):
  - kyc_api_requests_total: Request counter labeled by status
  - kyc_api_errors_total: Error counter labeled by error type
  - kyc_api_inference_latency_ms: Scoring latency histogram
  - kyc_feature_store_write_latency_ms: Feature store write latency histogram
  - /metrics endpoint for Prometheus scraping

Usage:
    uvicorn api:app --reload --port 8001

Then:
    curl -X POST http://localhost:8001/score -H "Content-Type: application/json" -d "{...}"
    curl http://localhost:8001/metrics
"""

import time
from contextlib import asynccontextmanager
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Response, Request
from pydantic import BaseModel
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from db_config import get_engine
from kafka_consumer_etl import (
    load_model,
    compute_feature_ranges,
    engineer_features_single,
    write_score,
    FEATURE_COLUMNS,
    FEATURE_STORE_WRITE_LATENCY,
)

# --- Prometheus Observability Metrics ---
API_REQUESTS = Counter(
    "kyc_api_requests_total",
    "Total scoring HTTP requests processed by FastAPI",
    ["method", "endpoint", "status_code"],
)
API_ERRORS = Counter(
    "kyc_api_errors_total",
    "Total scoring HTTP errors encountered",
    ["endpoint", "error_type"],
)
API_INFERENCE_LATENCY = Histogram(
    "kyc_api_inference_latency_ms",
    "Per-request feature engineering + model scoring latency in milliseconds",
    buckets=(5, 10, 20, 30, 50, 75, 100, 200, 500, 1000),
)

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Loads the model, DB engine, and feature-normalization ranges ONCE
    at startup -- same pattern as kafka_consumer_etl.py's main(), so
    every request doesn't re-query kyc_transactions for ranges or
    re-load the model file from disk.
    """
    print("Starting up: loading model, DB engine, and feature ranges...")
    _state["engine"] = get_engine()
    _state["model"] = load_model()
    _state["ranges"] = compute_feature_ranges(_state["engine"])
    print("Startup complete -- ready to score requests with Prometheus observability.")
    yield
    _state.clear()
    print("Shutdown complete.")


app = FastAPI(
    title="KYC Behavioral Observability -- Scoring API",
    description="Synchronous scoring endpoint for the Behavioral Observability Framework. "
                "Instrumented with Prometheus metrics for inference latency, request rate, and errors.",
    version="1.1.0",
    lifespan=lifespan,
)


class OnboardingEvent(BaseModel):
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


@app.get("/metrics")
def metrics():
    """Prometheus metrics scrape endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health():
    """Liveness/readiness probe -- confirms the model actually loaded."""
    return {
        "status": "ok" if "model" in _state else "starting",
        "model_loaded": "model" in _state,
    }


@app.post("/score", response_model=ScoreResponse)
def score(event: OnboardingEvent):
    if "model" not in _state:
        API_ERRORS.labels(endpoint="/score", error_type="model_not_ready").inc()
        API_REQUESTS.labels(method="POST", endpoint="/score", status_code="533").inc()
        raise HTTPException(status_code=503, detail="Model not loaded yet -- try again shortly.")

    start = time.perf_counter()
    event_dict = event.model_dump()

    try:
        features = engineer_features_single(event_dict, _state["ranges"])
    except Exception as e:
        API_ERRORS.labels(endpoint="/score", error_type="feature_engineering_failure").inc()
        API_REQUESTS.labels(method="POST", endpoint="/score", status_code="400").inc()
        raise HTTPException(status_code=400, detail=f"Feature engineering failed: {e}")

    try:
        X = pd.DataFrame([features])[FEATURE_COLUMNS]
        anomaly_score = float(-_state["model"].decision_function(X)[0])
        is_anomaly = bool(_state["model"].predict(X)[0] == -1)
        latency_ms = (time.perf_counter() - start) * 1000

        # Record API inference latency
        API_INFERENCE_LATENCY.observe(latency_ms)

        # Feature Store write with latency observation
        fs_start = time.perf_counter()
        write_score(
            _state["engine"], event.row_id, features, anomaly_score,
            is_anomaly, event.fraud_bool, latency_ms,
        )
        fs_latency_ms = (time.perf_counter() - fs_start) * 1000
        FEATURE_STORE_WRITE_LATENCY.observe(fs_latency_ms)

        API_REQUESTS.labels(method="POST", endpoint="/score", status_code="200").inc()

        return ScoreResponse(
            row_id=event.row_id,
            anomaly_score=anomaly_score,
            is_anomaly=is_anomaly,
            features=features,
            latency_ms=round(latency_ms, 2),
        )
    except Exception as e:
        API_ERRORS.labels(endpoint="/score", error_type="scoring_exception").inc()
        API_REQUESTS.labels(method="POST", endpoint="/score", status_code="500").inc()
        raise HTTPException(status_code=500, detail=f"Scoring error: {e}")


@app.get("/")
def root():
    return {
        "service": "KYC Behavioral Observability -- Scoring API",
        "docs": "/docs",
        "health": "/health",
        "metrics": "/metrics",
    }

