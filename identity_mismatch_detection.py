"""
identity_mismatch_detection.py
Layer 5 -- Identity Mismatch Detection
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Closes out Layer 5's last box: "Biometric + document consistency check."
Combines two independent signals already validated elsewhere in this
project into one identity-consistency decision:

  1. Document identity: the name extracted from an ID document via OCR
     (document_ocr.py) vs. the name the applicant CLAIMED on their
     onboarding application.
  2. Biometric identity: a face-match score (the same kind of score
     produced by biometric_face_matching.py) between the applicant's
     selfie and their ID document photo.

SCOPE NOTE (honest, same pattern as biometric_face_matching.py /
biometric_liveness_detection.py): there is no real dataset linking a
specific applicant's claimed name, ID document, and selfie together,
so this script demonstrates the DECISION LOGIC using synthetic paired
records -- some deliberately consistent, some deliberately mismatched
(name typos, wrong face) -- so the detection logic can be verified
against known ground truth, the same honest-PoC approach used
throughout Layer 5.

DECISION RULE (documented, simple, appropriate for a PoC):
  - name_similarity below NAME_MISMATCH_THRESHOLD -> document mismatch flag
  - face_match_score below FACE_MISMATCH_THRESHOLD -> biometric mismatch flag
  - either flag raised -> overall IDENTITY_MISMATCH = True

Usage:
    python identity_mismatch_detection.py --n-samples 50

Requires:
    pip install pandas (already installed)
"""

import argparse
import difflib
import random

import numpy as np
import pandas as pd
from sqlalchemy import text

from db_config import get_engine
from provenance import log_provenance

OUTPUT_TABLE = "identity_mismatch_results"

NAME_MISMATCH_THRESHOLD = 0.80   # below this string-similarity, flag a document mismatch
FACE_MISMATCH_THRESHOLD = 0.50   # below this face-match score, flag a biometric mismatch

FIRST_NAMES = ["Amit", "Priya", "Rahul", "Sneha", "Vikram", "Anjali", "Rohan", "Kavya",
               "Arjun", "Divya", "Karan", "Neha", "Suresh", "Pooja", "Manish"]
LAST_NAMES = ["Sharma", "Verma", "Gupta", "Reddy", "Iyer", "Nair", "Rao", "Singh",
              "Patel", "Kumar", "Joshi", "Mehta", "Desai", "Kapoor", "Malhotra"]

CREATE_OUTPUT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {OUTPUT_TABLE} (
    id SERIAL PRIMARY KEY,
    applicant_id TEXT,
    claimed_name TEXT,
    document_name TEXT,
    name_similarity DOUBLE PRECISION,
    document_mismatch BOOLEAN,
    face_match_score DOUBLE PRECISION,
    biometric_mismatch BOOLEAN,
    identity_mismatch BOOLEAN,
    scenario TEXT,
    processed_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


def name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def introduce_typo(name: str) -> str:
    """Simulates a plausible clerical mismatch (e.g. OCR error or data-entry typo)."""
    if len(name) < 3:
        return name
    pos = random.randint(1, len(name) - 2)
    chars = list(name)
    chars[pos] = random.choice("abcdefghijklmnopqrstuvwxyz")
    return "".join(chars)


def generate_synthetic_case(applicant_id: str, rng: np.random.Generator) -> dict:
    """
    Generates one applicant record under one of three ground-truth
    scenarios, so detection accuracy can be verified against a KNOWN
    label -- same principle as the OCR ground-truth approach.
    """
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    claimed_name = f"{first} {last}"

    scenario = random.choices(
        ["consistent", "document_mismatch", "biometric_mismatch"],
        weights=[0.70, 0.15, 0.15],  # most applicants are genuine, mirrors real KYC populations
    )[0]

    if scenario == "consistent":
        document_name = claimed_name
        face_match_score = float(np.clip(rng.normal(0.85, 0.08), 0, 1))
    elif scenario == "document_mismatch":
        document_name = introduce_typo(claimed_name) if random.random() < 0.5 else \
            f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        face_match_score = float(np.clip(rng.normal(0.80, 0.08), 0, 1))  # face is fine, name is not
    else:  # biometric_mismatch
        document_name = claimed_name
        face_match_score = float(np.clip(rng.normal(0.25, 0.10), 0, 1))  # name matches, face doesn't

    return {
        "applicant_id": applicant_id,
        "claimed_name": claimed_name,
        "document_name": document_name,
        "face_match_score": face_match_score,
        "scenario": scenario,  # ground truth, kept for accuracy scoring, not used by the detector itself
    }


def detect_mismatch(case: dict) -> dict:
    sim = name_similarity(case["claimed_name"], case["document_name"])
    doc_mismatch = sim < NAME_MISMATCH_THRESHOLD
    bio_mismatch = case["face_match_score"] < FACE_MISMATCH_THRESHOLD
    overall = doc_mismatch or bio_mismatch

    return {
        **case,
        "name_similarity": sim,
        "document_mismatch": doc_mismatch,
        "biometric_mismatch": bio_mismatch,
        "identity_mismatch": overall,
    }


def main():
    parser = argparse.ArgumentParser(description="Layer 5: Identity mismatch detection")
    parser.add_argument("--n-samples", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print("=" * 65)
    print("LAYER 5 -- Identity Mismatch Detection")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    random.seed(args.seed)
    rng = np.random.default_rng(args.seed)

    print(f"\nGenerating {args.n_samples} synthetic applicant identity cases "
          f"(consistent / document-mismatch / biometric-mismatch scenarios)...")

    results = []
    for i in range(args.n_samples):
        case = generate_synthetic_case(f"applicant_{i:04d}", rng)
        result = detect_mismatch(case)
        results.append(result)

    results_df = pd.DataFrame(results)

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text(CREATE_OUTPUT_TABLE_SQL))
        conn.commit()
    results_df.to_sql(OUTPUT_TABLE, engine, if_exists="replace", index=False, method="multi")
    print(f"Wrote {len(results_df)} results to '{OUTPUT_TABLE}'")

    # Accuracy against the known ground-truth scenario label -- this is
    # what makes the validation genuine rather than just "it ran".
    correctly_flagged_mismatch = results_df[
        (results_df["scenario"] != "consistent") & (results_df["identity_mismatch"])
    ]
    total_actual_mismatches = results_df[results_df["scenario"] != "consistent"]
    correctly_passed_consistent = results_df[
        (results_df["scenario"] == "consistent") & (~results_df["identity_mismatch"])
    ]
    total_actual_consistent = results_df[results_df["scenario"] == "consistent"]

    detection_rate = len(correctly_flagged_mismatch) / max(len(total_actual_mismatches), 1)
    false_positive_rate = 1 - (len(correctly_passed_consistent) / max(len(total_actual_consistent), 1))

    print("\n" + "=" * 65)
    print("IDENTITY MISMATCH DETECTION -- VALIDATION SUMMARY")
    print("=" * 65)
    print(f"Total cases:                          {len(results_df)}")
    print(f"  Consistent (ground truth):          {len(total_actual_consistent)}")
    print(f"  Mismatched (ground truth):          {len(total_actual_mismatches)}")
    print(f"Detection rate (true mismatches caught): {detection_rate:.1%}")
    print(f"False positive rate (consistent flagged as mismatch): {false_positive_rate:.1%}")
    print("=" * 65)

    print("\nBreakdown by scenario:")
    print(results_df.groupby("scenario")["identity_mismatch"].agg(["sum", "count"]))

    log_provenance(
        engine,
        script_name="identity_mismatch_detection.py",
        source_dataset="synthetic_identity_cases (generated)",
        target_table=OUTPUT_TABLE,
        row_count=len(results_df),
        notes=f"detection_rate={detection_rate:.1%}, false_positive_rate={false_positive_rate:.1%}",
    )

    print("\n[DONE] See 'identity_mismatch_results' table for full per-applicant results.")


if __name__ == "__main__":
    main()
