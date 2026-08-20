"""
biometric_features.py
Layer 5 (Biometric Authentication) -- SYNTHETIC placeholder.

IMPORTANT / FOR YOUR METHODOLOGY SECTION:
------------------------------------------
BAF (and every other public KYC fraud dataset) contains NO real face
images, ID documents, or liveness signals -- only tabular behavioral
data. There is no public dataset that legally combines real faces +
documents + financial fraud history in one file (privacy regulation
prevents it).

This module SIMULATES what a real DeepFace/FaceNet + Tesseract OCR
pipeline (Layer 5) would output, using statistically realistic score
distributions that correlate with fraud_bool the way real biometric
signals do in practice: genuine applicants score high and tight,
fraudulent applicants score lower and more spread out (some fraud
attempts still pass, mirroring real-world imperfect liveness checks).

Replace this module with real DeepFace/Tesseract output once you have
image data (see README section "Swapping in real biometric data").
Document this file's use plainly in your dissertation as a modelled
stand-in for Layer 5, pending real biometric datasets.
"""

import numpy as np
import pandas as pd

RANDOM_STATE = 42


def _beta_scores(n: int, a: float, b: float, rng: np.random.Generator) -> np.ndarray:
    """Beta distribution keeps every score bounded to [0, 1], like a real confidence score."""
    return rng.beta(a, b, size=n)


def synthesize_biometric_features(df: pd.DataFrame, fraud_col: str = "fraud_bool",
                                   random_state: int = RANDOM_STATE) -> pd.DataFrame:
    """
    Adds three synthetic biometric columns to df:
        liveness_score        -- probability a real, live person was present (0-1)
        face_match_score      -- selfie-to-ID similarity (0-1)
        ocr_confidence_score  -- document text-extraction confidence (0-1)
    and one composite:
        biometric_risk_score  -- higher = more suspicious (0-1)

    Genuine applications (fraud_bool == 0) get high, tightly-clustered
    scores. Fraudulent applications (fraud_bool == 1) get lower, more
    spread-out scores -- but with overlap, since real fraud sometimes
    passes biometric checks (that overlap is intentional and realistic).
    """
    print("Synthesizing biometric features (Layer 5 placeholder -- SEE MODULE DOCSTRING)...")
    rng = np.random.default_rng(random_state)
    n = len(df)

    is_fraud = df[fraud_col].to_numpy() if fraud_col in df.columns else np.zeros(n, dtype=int)
    n_fraud = int(is_fraud.sum())
    n_legit = n - n_fraud

    liveness = np.empty(n)
    face_match = np.empty(n)
    ocr_conf = np.empty(n)

    legit_idx = np.where(is_fraud == 0)[0]
    fraud_idx = np.where(is_fraud == 1)[0]

    # Genuine applicants: high, tight distributions
    liveness[legit_idx] = _beta_scores(n_legit, 50, 3, rng)
    face_match[legit_idx] = _beta_scores(n_legit, 40, 5, rng)
    ocr_conf[legit_idx] = _beta_scores(n_legit, 30, 3, rng)

    # Fraudulent applicants: lower, wider distributions (deliberate overlap with genuine)
    liveness[fraud_idx] = _beta_scores(n_fraud, 8, 4, rng)
    face_match[fraud_idx] = _beta_scores(n_fraud, 5, 5, rng)
    ocr_conf[fraud_idx] = _beta_scores(n_fraud, 15, 5, rng)

    out = df.copy()
    out["liveness_score"] = liveness
    out["face_match_score"] = face_match
    out["ocr_confidence_score"] = ocr_conf

    # Composite: higher = riskier (inverse of the three scores, weighted)
    out["biometric_risk_score"] = (
        0.40 * (1 - out["liveness_score"])
        + 0.35 * (1 - out["face_match_score"])
        + 0.25 * (1 - out["ocr_confidence_score"])
    )

    if n_legit and n_fraud:
        print(f"  liveness_score        mean(legit)={liveness[legit_idx].mean():.3f}  mean(fraud)={liveness[fraud_idx].mean():.3f}")
        print(f"  face_match_score      mean(legit)={face_match[legit_idx].mean():.3f}  mean(fraud)={face_match[fraud_idx].mean():.3f}")
        print(f"  ocr_confidence_score  mean(legit)={ocr_conf[legit_idx].mean():.3f}  mean(fraud)={ocr_conf[fraud_idx].mean():.3f}")
    print("  [OK] biometric_risk_score (composite)")

    return out


if __name__ == "__main__":
    # Quick standalone sanity check
    demo = pd.DataFrame({"fraud_bool": [0] * 9000 + [1] * 1000})
    result = synthesize_biometric_features(demo)
    print(result.groupby("fraud_bool")[
        ["liveness_score", "face_match_score", "ocr_confidence_score", "biometric_risk_score"]
    ].mean())
