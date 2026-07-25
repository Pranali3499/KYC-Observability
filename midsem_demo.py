"""
midsem_demo.py
Orchestrator -- runs Demo Pieces 1-3 in sequence with labelled output.
(Demo Piece 4 is launched separately via `streamlit run dashboard.py`
since it's a long-running web server, not a one-shot script.)

Usage:
    python midsem_demo.py --csv path/to/Base.csv
"""

import argparse
import subprocess
import sys


def run_step(cmd: list, label: str):
    print("\n" + "=" * 65)
    print(label)
    print("=" * 65)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"[FAIL] {label} exited with code {result.returncode}")
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description="Run the full mid-sem demo pipeline")
    parser.add_argument("--csv", required=True, help="Path to BAF Base.csv")
    args = parser.parse_args()

    print("=" * 65)
    print("Behavioral Observability Framework for KYC Onboarding")
    print("Student: Pranali Pandharinath Supekar (2024DA04387)")
    print("Dataset: Bank Account Fraud (BAF) | DB: PostgreSQL 17 | Model: Isolation Forest")
    print("=" * 65)

    run_step([sys.executable, "data_ingestion.py", "--csv", args.csv], "DEMO PIECE 1 -- Data Ingestion")
    run_step([sys.executable, "feature_engineering.py"], "DEMO PIECE 2 -- Feature Engineering")
    run_step([sys.executable, "anomaly_detection.py"], "DEMO PIECE 3 -- Isolation Forest Anomaly Detection")

    print("\n" + "=" * 65)
    print("OVERALL MID-SEM RESULTS")
    print("=" * 65)
    print("Demo Pieces 1-3 completed successfully.")
    print("Run 'streamlit run dashboard.py' to launch Demo Piece 4 (visualization).")


if __name__ == "__main__":
    main()
