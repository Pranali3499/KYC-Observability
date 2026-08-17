"""
canary_rollout_simulator.py
Canary Deployment & Automated Rollback Simulator
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Simulates a progressive canary deployment of a newly retrained candidate model:
  1. Phase 1: 90% Champion / 10% Canary traffic split
  2. Phase 2: Live health gate verification (P95 latency <= 100ms, error rate <= 5%)
  3. Phase 3: Progressive promotion (50% -> 100%) or Automated Rollback if health checks breach thresholds.

Responds directly to mid-sem evaluator feedback:
"Deploy continuous performance metrics, drift detection (PSI/KS), and alerts;
schedule retraining or canary rollouts when degradation detected."

Usage:
  python canary_rollout_simulator.py
  python canary_rollout_simulator.py --simulate-failure
"""

import argparse
import os
import random
import time

import joblib
import mlflow
import numpy as np
import pandas as pd

from provenance import log_provenance
from db_config import get_engine

CHAMPION_PATH = "isolation_forest_tuned.pkl"
CANDIDATE_PATH = "isolation_forest_candidate.pkl"
MLFLOW_EXPERIMENT = "kyc-canary-rollouts"

FEATURE_COLUMNS = [
    "session_velocity_score",
    "device_reuse_score",
    "address_stability_score",
    "identity_consistency_score",
    "geographic_risk_score",
    "financial_risk_score",
]


def load_test_features(engine, n_records: int = 500) -> pd.DataFrame:
    df = pd.read_sql(f"SELECT * FROM behavioral_features LIMIT {n_records}", engine)
    return df[FEATURE_COLUMNS]


def run_canary_simulation(simulate_failure: bool = False) -> bool:
    print("=" * 65)
    print("CANARY ROLLOUT & AUTOMATED ROLLBACK SIMULATION")
    print("=" * 65)

    if not os.path.exists(CHAMPION_PATH):
        print(f"[ERROR] Champion model '{CHAMPION_PATH}' not found.")
        return False

    champion = joblib.load(CHAMPION_PATH)

    # Use candidate if exists, else fallback to champion as standalone
    if os.path.exists(CANDIDATE_PATH):
        candidate = joblib.load(CANDIDATE_PATH)
        print("Loaded Champion and Candidate models.")
    else:
        candidate = champion
        print("Candidate model not found -- simulating candidate from tuned model.")

    engine = get_engine()
    test_df = load_test_features(engine, n_records=600)
    print(f"Loaded {len(test_df)} live scoring requests for canary simulation.")

    stages = [
        {"name": "Stage 1 (10% Canary)", "canary_weight": 0.10, "n_requests": 200},
        {"name": "Stage 2 (50% Canary)", "canary_weight": 0.50, "n_requests": 200},
        {"name": "Stage 3 (100% Full Promotion)", "canary_weight": 1.00, "n_requests": 200},
    ]

    all_passed = True
    rollout_log = []

    for stage in stages:
        s_name = stage["name"]
        weight = stage["canary_weight"]
        n_req = stage["n_requests"]

        print(f"\n--- Running {s_name} (Traffic Split: {int((1-weight)*100)}% Champ / {int(weight*100)}% Canary) ---")

        canary_latencies = []
        canary_errors = 0
        champ_latencies = []

        for i in range(n_req):
            row = test_df.iloc[[i % len(test_df)]]
            route_to_canary = random.random() < weight

            t0 = time.perf_counter()
            if route_to_canary:
                # Simulate potential failure condition if flag is set
                if simulate_failure and i > 50:
                    time.sleep(0.12)  # Inject latency spike (>100ms)
                    if random.random() < 0.10:
                        canary_errors += 1

                _ = -candidate.decision_function(row)[0]
                lat = (time.perf_counter() - t0) * 1000
                canary_latencies.append(lat)
            else:
                _ = -champion.decision_function(row)[0]
                lat = (time.perf_counter() - t0) * 1000
                champ_latencies.append(lat)

        canary_p95 = np.percentile(canary_latencies, 95) if canary_latencies else 0.0
        canary_err_rate = (canary_errors / len(canary_latencies) * 100) if canary_latencies else 0.0
        champ_p95 = np.percentile(champ_latencies, 95) if champ_latencies else 0.0

        print(f"  Champion P95 Latency: {champ_p95:.2f}ms")
        print(f"  Canary   P95 Latency: {canary_p95:.2f}ms (Target: <= 100.0ms)")
        print(f"  Canary   Error Rate : {canary_err_rate:.2f}% (Target: <= 5.0%)")

        # Health Gate Check
        gate_passed = (canary_p95 <= 100.0) and (canary_err_rate <= 5.0)

        rollout_log.append({
            "stage": s_name,
            "canary_p95_ms": canary_p95,
            "canary_err_rate_pct": canary_err_rate,
            "gate_passed": gate_passed,
        })

        if not gate_passed:
            print(f"\n  [ALERT] Canary health gate BREACHED at {s_name}!")
            print("  >>> AUTOMATED ROLLBACK INITIATED: Routing 100% traffic back to Champion.")
            print("  >>> Candidate promotion aborted.")
            all_passed = False
            break
        else:
            print(f"  [PASS] Health gate passed. Advancing to next stage...")

    if all_passed:
        print("\n" + "=" * 65)
        print("CANARY ROLLOUT VERDICT: [SUCCESS - CANDIDATE PROMOTED TO CHAMPION]")
        print("=" * 65)
    else:
        print("\n" + "=" * 65)
        print("CANARY ROLLOUT VERDICT: [ROLLBACK EXECUTED - CHAMPION PRESERVED]")
        print("=" * 65)

    # MLflow Logging
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    with mlflow.start_run(run_name=f"canary_simulation_{time.strftime('%Y%m%d_%H%M%S')}"):
        mlflow.log_param("simulation_mode", "Failure_Test" if simulate_failure else "Normal_Rollout")
        mlflow.log_param("final_verdict", "PROMOTED" if all_passed else "ROLLED_BACK")
        for log in rollout_log:
            stg = log["stage"].split()[0].lower()
            mlflow.log_metric(f"{stg}_canary_p95_ms", log["canary_p95_ms"])
            mlflow.log_metric(f"{stg}_canary_error_pct", log["canary_err_rate_pct"])

    return all_passed


def main():
    parser = argparse.ArgumentParser(description="Canary rollout and automated rollback simulator")
    parser.add_argument("--simulate-failure", action="store_true", help="Simulate a canary performance breach to demonstrate rollback")
    args = parser.parse_args()

    run_canary_simulation(args.simulate_failure)


if __name__ == "__main__":
    main()
