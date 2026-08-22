"""
run_all_pipeline.py
Master End-to-End Orchestrator for KYC Behavioral Observability Framework
Student: Pranali Pandharinath Supekar (2024DA04387) | M.Tech DSE, BITS Pilani WILP

Runs the entire KYC Observability project locally or in CI/CD (Jenkins, GitHub Actions)
from start to finish with a single trigger:
  Stage 0: Docker Infrastructure & Environment Verification
  Stage 1: Full Test Pyramid (55 Automated Tests with JUnit XML Export)
  Stage 2: Pre-Ingestion Data Validation & Deduplication Gate
  Stage 3: Behavioral Feature Engineering & Database Quality Gate
  Stage 4: Cross-Dataset Generalization Evaluation (Base + Variant I-V)
  Stage 5: SHAP Explainability & Counterfactual Recourse Analysis
  Stage 6: Biometric Sub-components, Parquet ETL & Go/No-Go Decision Gate
  Stage 7: Real-Time Kafka Streaming & Consumer Scoring (Onboarding + Biometrics)
  Stage 8: Continuous Drift Detection, Retraining & Progressive Canary Rollout
  Stage 9: Observability Health & Summary Dashboard Metrics

Usage:
  python run_all_pipeline.py                      # Full production run
  python run_all_pipeline.py --mode fast          # Fast demo run (~2 mins for viva)
  python run_all_pipeline.py --mode ci            # CI unit & static tests only
  python run_all_pipeline.py --skip-docker        # Skip docker checks if already running
"""

import os
import sys
import time
import argparse
import subprocess
from datetime import datetime

PYTHON_EXE = sys.executable
STAGE_METRICS = []

def print_banner(title):
    print("\n" + "=" * 78)
    print(f"  >>> {title.upper()} <<<")
    print("=" * 78)

def run_step(step_name, command, cwd=None, allow_failure=False):
    print(f"\n[*] RUNNING: {step_name}")
    cmd_display = ' '.join(command) if isinstance(command, list) else command
    print(f"    Command: {cmd_display}")
    t0 = time.time()
    try:
        if isinstance(command, str):
            res = subprocess.run(command, shell=True, check=not allow_failure, cwd=cwd)
        else:
            res = subprocess.run(command, check=not allow_failure, cwd=cwd)
        elapsed = time.time() - t0
        passed = (res.returncode == 0)
        status_str = "[PASS]" if passed else "[WARN]"
        print(f"{status_str} COMPLETED: {step_name} in {elapsed:.2f}s (Exit Code: {res.returncode})")
        STAGE_METRICS.append((step_name, f"{elapsed:.2f}s", "PASS" if passed else "WARN"))
        return passed
    except subprocess.CalledProcessError as e:
        elapsed = time.time() - t0
        print(f"[-] FAILED: {step_name} in {elapsed:.2f}s (Exit Code: {e.returncode})")
        STAGE_METRICS.append((step_name, f"{elapsed:.2f}s", "FAILED"))
        if not allow_failure:
            print(f"\n[!] Pipeline stopped due to critical failure in '{step_name}'.")
            print_summary(success=False)
            sys.exit(1)
        return False

def print_summary(success=True):
    print("\n" + "=" * 78)
    print("           PIPELINE EXECUTION SUMMARY & STAGE TELEMETRY")
    print("=" * 78)
    print(f"{'Stage / Step Name':<55} | {'Duration':<10} | {'Status':<8}")
    print("-" * 78)
    for name, dur, status in STAGE_METRICS:
        status_tag = f"[{status}]"
        print(f"{name:<55} | {dur:<10} | {status_tag:<8}")
    print("=" * 78)
    if success:
        print("[+] ALL STAGES EXECUTED SUCCESSFULLY WITH A SINGLE TRIGGER!")
    else:
        print("[-] PIPELINE FAILED AT ONE OR MORE CRITICAL GATES.")
    print("=" * 78)

def main():
    parser = argparse.ArgumentParser(description="Master Single-Trigger Pipeline Orchestrator for KYC Observability")
    parser.add_argument("--mode", choices=["full", "fast", "ci"], default="full",
                        help="Execution mode: full (1M rows evaluation), fast (sample demo for viva), ci (tests only)")
    parser.add_argument("--skip-docker", action="store_true", help="Skip Docker container health checks")
    parser.add_argument("--junit", action="store_true", default=True, help="Generate JUnit XML test reports for Jenkins/CI")
    args = parser.parse_args()

    start_time = time.time()
    print("=" * 78)
    print("  KYC BEHAVIORAL OBSERVABILITY FRAMEWORK -- SINGLE-TRIGGER PIPELINE")
    print("  Student: Pranali Pandharinath Supekar (2024DA04387) | M.Tech DSE, BITS Pilani")
    print(f"  Mode: {args.mode.upper()} | Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)

    # -------------------------------------------------------------------------
    # Stage 0: Docker Infrastructure Check
    # -------------------------------------------------------------------------
    if not args.skip_docker and args.mode != "ci":
        print_banner("Stage 0: Checking Docker Container Stack")
        run_step("Start Docker Infrastructure", "docker compose up -d", allow_failure=True)
        run_step("Verify Docker Containers", "docker ps", allow_failure=True)

    # -------------------------------------------------------------------------
    # Stage 1: Full Test Pyramid (55 Tests + JUnit XML)
    # -------------------------------------------------------------------------
    print_banner("Stage 1: Automated Test Pyramid Verification (55 Tests)")
    os.makedirs("test-reports", exist_ok=True)
    pytest_cmd = [PYTHON_EXE, "-m", "pytest", "tests/", "-v"]
    if args.junit:
        pytest_cmd.extend(["--junitxml=test-reports/pytest-results.xml", "--tb=short"])
    run_step("Pytest Automated Test Suite (Unit, Regression, E2E)", pytest_cmd)

    if args.mode == "ci":
        print("\n[+] CI Mode completed successfully.")
        print_summary(success=True)
        return

    # Determine sample sizes based on mode
    val_sample_size = "25000" if args.mode == "fast" else "50000"
    cross_sample_size = "10000" if args.mode == "fast" else "50000"
    shap_samples = "500" if args.mode == "fast" else "2000"
    cf_samples = "100" if args.mode == "fast" else "500"
    n_onboard_events = "50" if args.mode == "fast" else "100"
    n_bio_events = "25" if args.mode == "fast" else "50"

    # -------------------------------------------------------------------------
    # Stage 2: Pre-Ingestion Data Validation & Deduplication Gate
    # -------------------------------------------------------------------------
    print_banner("Stage 2: Pre-Ingestion Validation & Deduplication Gate")
    run_step("Pre-Ingestion Validator (SHA-256 Dedup + Contract Gate)",
             [PYTHON_EXE, "pre_ingestion_validator.py", "--csv", "Base.csv", "--sample-size", val_sample_size])

    # -------------------------------------------------------------------------
    # Stage 3: Feature Engineering & Database Data Quality Checks
    # -------------------------------------------------------------------------
    print_banner("Stage 3: Behavioral Feature Engineering & Data Quality Checks")
    feat_sample_size = "25000" if args.mode == "fast" else "50000"
    run_step("Behavioral Feature Engineering (Risk Scores)",
             [PYTHON_EXE, "feature_engineering.py", "--sample-size", feat_sample_size])
    run_step("Post-Ingestion Data Quality Checks", [PYTHON_EXE, "data_quality_checks.py"])

    # -------------------------------------------------------------------------
    # Stage 4: Cross-Dataset Generalization Evaluation (Base + Variant I-V)
    # -------------------------------------------------------------------------
    print_banner("Stage 4: Cross-Dataset Generalization Evaluation (Base + Variant I-V)")
    run_step("Cross-Dataset Shift Evaluation",
             [PYTHON_EXE, "cross_dataset_evaluation.py", "--sample-size", cross_sample_size])

    # -------------------------------------------------------------------------
    # Stage 5: Explainability & Recourse Analysis (SHAP + Counterfactuals)
    # -------------------------------------------------------------------------
    print_banner("Stage 5: SHAP Explainability & Counterfactual Recourse Analysis")
    run_step("SHAP Global & Local Feature Attributions",
             [PYTHON_EXE, "shap_explainability.py", "--n-samples", shap_samples])
    run_step("Counterfactual Recourse Analysis",
             [PYTHON_EXE, "counterfactual_analysis.py", "--n-samples", cf_samples])

    # -------------------------------------------------------------------------
    # Stage 6: Biometric Sub-components, Parquet ETL & Go/No-Go Gate
    # -------------------------------------------------------------------------
    print_banner("Stage 6: Biometric Sub-components, Parquet ETL & Decision Gate")
    run_step("Face Matching Sub-component", [PYTHON_EXE, "biometric_face_matching.py"])
    run_step("Liveness Detection Sub-component", [PYTHON_EXE, "biometric_liveness_detection.py"])
    run_step("Document OCR Sub-component", [PYTHON_EXE, "document_ocr.py"])
    run_step("Identity Mismatch Detection", [PYTHON_EXE, "identity_mismatch_detection.py"])
    run_step("Biometric Parquet Normalization ETL", [PYTHON_EXE, "biometric_etl_normalize.py"])
    run_step("Biometric Parquet Combination ETL", [PYTHON_EXE, "biometric_etl_combine.py"])
    run_step("Automated Biometric Go/No-Go Decision Gate", [PYTHON_EXE, "verify_biometric_go_no_go.py"])

    # -------------------------------------------------------------------------
    # Stage 7: Real-Time Kafka Streaming & Scoring ETL (Onboarding + Biometrics)
    # -------------------------------------------------------------------------
    print_banner("Stage 7: Real-Time Kafka Streaming & Scoring ETL")
    run_step(f"Kafka Onboarding Event Producer ({n_onboard_events} events)",
             [PYTHON_EXE, "kafka_producer.py", "--n-events", n_onboard_events, "--delay", "0.01"])
    run_step(f"Kafka Onboarding Consumer ETL ({n_onboard_events} messages)",
             [PYTHON_EXE, "kafka_consumer_etl.py", "--max-messages", n_onboard_events])
    run_step(f"Kafka Biometric Event Producer ({n_bio_events} events)",
             [PYTHON_EXE, "kafka_biometric_producer.py", "--n-events", n_bio_events, "--delay", "0.01"])
    run_step(f"Kafka Biometric Consumer ETL ({n_bio_events} messages)",
             [PYTHON_EXE, "kafka_biometric_consumer_etl.py", "--max-messages", n_bio_events])

    # -------------------------------------------------------------------------
    # Stage 8: Continuous Drift Detection, Retraining & Progressive Canary Rollout
    # -------------------------------------------------------------------------
    print_banner("Stage 8: Continuous Drift Detection, Retraining & Canary Rollout")
    run_step("Continuous Drift Detection (10-bin PSI & 2-sample KS)", [PYTHON_EXE, "drift_detection.py"])
    run_step("Drift Metrics Exporter (Prometheus Gauge Push)", [PYTHON_EXE, "drift_metrics_exporter.py", "--once"])
    run_step("Automated Retraining Pipeline (Drift-Triggered)", [PYTHON_EXE, "retraining_pipeline.py", "--simulate-drift"])
    run_step("Progressive Canary Rollout Simulator (10% -> 50% -> 100%)", [PYTHON_EXE, "canary_rollout_simulator.py"])

    # -------------------------------------------------------------------------
    # Stage 9: Summary & Observability Links
    # -------------------------------------------------------------------------
    total_elapsed = time.time() - start_time
    print_banner("Stage 9: Complete Pipeline Verification Summary")
    print_summary(success=True)
    print(f"\nTotal Pipeline Execution Time: {total_elapsed:.2f}s ({total_elapsed/60:.2f} mins)")
    print("\n--- Interactive Dashboards & Metrics Endpoints ---")
    print("  • Grafana Unified Dashboard:   http://localhost:3000  (admin / admin)")
    print("  • Prometheus Targets & Alerts:  http://localhost:9090/alerts")
    print("  • Kafka Web UI Manager:        http://localhost:8080")
    print("  • FastAPI Scoring Swagger Docs: http://localhost:8001/docs")
    print("  • Streaming Consumer Metrics:   http://localhost:8000/metrics")
    print("  • FastAPI Prometheus Metrics:   http://localhost:8001/metrics")
    print("  • Drift Exporter Metrics:       http://localhost:8002/metrics")
    print("  • Biometric Consumer Metrics:   http://localhost:8003/metrics")
    print("  • Node Resource Metrics:       http://localhost:9100/metrics")
    print("=" * 78)

if __name__ == "__main__":
    main()
