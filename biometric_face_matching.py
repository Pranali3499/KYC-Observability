"""
biometric_face_matching.py
Stage 4 -- Biometric Validation: Face Matching
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Validates the face-matching sub-component of Layer 5 using LFW
(Labeled Faces in the Wild) -- a public face-pair dataset that ships
directly through scikit-learn, no Kaggle download required.

IMPORTANT -- why this is a SEPARATE validation, not a join with BAF:
LFW has no relationship to your BAF onboarding applicants -- there is
no shared applicant ID or any real-world link between a BAF row and
an LFW face pair. This script does not merge anything into
behavioral_features. It validates, independently, whether a basic
face-matching approach CAN distinguish same-person vs different-person
photo pairs -- i.e. it proves the biometric layer's technical
soundness using data appropriate for that specific question, which is
what the evaluator feedback's "cross-dataset ROC/AUC analysis" and
"FAR/FRR metrics" items are asking for.

Approach: a classic, defensible PoC baseline -- absolute pixel-wise
difference between each pair, dimensionality-reduced with PCA, fed to
a Logistic Regression classifier. This is not state-of-the-art face
recognition (that would be a deep embedding model, e.g. FaceNet), but
it is a legitimate, explainable baseline for a dissertation-level PoC,
and the report can note deep embeddings as future work.

--- CHANGE LOG (added to close mid-sem evaluator feedback gap) ---
Added: per-record test-set results are now persisted to a
'face_match_results' table (previously y_test/y_score existed only
in memory and were discarded after computing aggregate AUC/FAR/FRR).
This is what lets biometric_etl_normalize.py pick this component up
alongside document_ocr_results and identity_mismatch_results, closing
the "produce feature-ready parquet files" gap for this sub-component.
No other logic in this script was changed.
--------------------------------------------------------------------

Usage:
    python biometric_face_matching.py

Requires:
    pip install scikit-learn mlflow matplotlib
First run downloads LFW automatically (~200MB, cached afterward in
~/scikit_learn_data/).
"""

import os

import matplotlib
matplotlib.use("Agg")  # no GUI backend needed, just saving figures
import matplotlib.pyplot as plt
import mlflow
import numpy as np
import pandas as pd
from sklearn.datasets import fetch_lfw_pairs
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve

from provenance import log_provenance
from db_config import get_engine

MLFLOW_EXPERIMENT_NAME = "kyc-biometric-validation"
MODEL_OUTPUT_PATH = "face_match_model.pkl"
ROC_FIGURE_PATH = "face_match_roc_curve.png"
RESULTS_TABLE = "face_match_results"

# FAR = False Accept Rate: different-person pairs wrongly called "same"
#       (a security risk -- lets an impostor through)
# FRR = False Reject Rate: same-person pairs wrongly called "different"
#       (a usability cost -- blocks a genuine applicant)
# Reported at several thresholds so the report can discuss the tradeoff,
# same framing as the AUC-vs-precision/recall tradeoff from Stage 2.
THRESHOLDS_TO_REPORT = [0.3, 0.4, 0.5, 0.6, 0.7]


def load_pairs():
    print("Loading LFW pairs (downloads automatically on first run)...")
    train = fetch_lfw_pairs(subset="train", color=True, resize=0.5, funneled=True)
    test = fetch_lfw_pairs(subset="test", color=True, resize=0.5, funneled=True)
    print(f"  Train pairs: {len(train.target):,}   Test pairs: {len(test.target):,}")
    return train, test


def pairs_to_features(pairs_data) -> np.ndarray:
    """
    pairs.pairs has shape (n_pairs, 2, h, w, 3) -- two images per pair.
    Feature = flattened absolute pixel difference between the two images
    in each pair. Same-person pairs should have smaller differences;
    different-person pairs larger -- that's the signal the classifier learns.
    """
    imgs = pairs_data.pairs
    diff = np.abs(imgs[:, 0] - imgs[:, 1])
    return diff.reshape(diff.shape[0], -1)


def far_frr_at_threshold(y_true: np.ndarray, y_score: np.ndarray, threshold: float) -> tuple[float, float]:
    y_pred = (y_score >= threshold).astype(int)
    # y_true: 1 = same person (genuine match), 0 = different person (impostor)
    different_mask = y_true == 0
    same_mask = y_true == 1

    far = (y_pred[different_mask] == 1).mean() if different_mask.any() else float("nan")
    frr = (y_pred[same_mask] == 0).mean() if same_mask.any() else float("nan")
    return far, frr


def write_per_record_results(engine, y_test: np.ndarray, y_score: np.ndarray) -> int:
    """
    Persists the test-set results this script already computes -- one
    row per LFW test pair, with its true label, the model's predicted
    match probability, and a default (threshold=0.5) pass/fail call.
    This did not exist before; y_test/y_score were previously discarded
    after being used only for aggregate AUC/FAR/FRR computation.
    """
    results_df = pd.DataFrame({
        "pair_index": range(len(y_test)),
        "true_label": y_test.astype(int),          # 1 = same person, 0 = different person
        "predicted_match_score": y_score,           # P(same person), i.e. face-match confidence
        "predicted_match": (y_score >= 0.5).astype(int),
    })
    results_df.to_sql(RESULTS_TABLE, engine, if_exists="replace", index=False)
    print(f"Wrote {len(results_df):,} per-pair results to '{RESULTS_TABLE}'")
    return len(results_df)


def main():
    print("=" * 65)
    print("STAGE 4 -- Biometric Validation: Face Matching (LFW)")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    train, test = load_pairs()

    print("\nBuilding features (pixel-difference + PCA)...")
    X_train_raw = pairs_to_features(train)
    X_test_raw = pairs_to_features(test)
    y_train, y_test = train.target, test.target

    pca = PCA(n_components=150, whiten=True, random_state=42)
    X_train = pca.fit_transform(X_train_raw)
    X_test = pca.transform(X_test_raw)
    print(f"  PCA: {X_train_raw.shape[1]} -> {X_train.shape[1]} dimensions "
          f"({pca.explained_variance_ratio_.sum():.1%} variance retained)")

    print("\nTraining face-match classifier (Logistic Regression baseline)...")
    clf = LogisticRegression(max_iter=1000, random_state=42)
    clf.fit(X_train, y_train)

    y_score = clf.predict_proba(X_test)[:, 1]  # P(same person)
    auc = roc_auc_score(y_test, y_score)
    fpr, tpr, roc_thresholds = roc_curve(y_test, y_score)

    print(f"\nTest set AUC: {auc:.4f}")

    print("\nFAR / FRR at several thresholds:")
    print(f"{'Threshold':<12}{'FAR':<10}{'FRR':<10}")
    far_frr_table = []
    for t in THRESHOLDS_TO_REPORT:
        far, frr = far_frr_at_threshold(y_test, y_score, t)
        print(f"{t:<12}{far:<10.4f}{frr:<10.4f}")
        far_frr_table.append({"threshold": t, "far": far, "frr": frr})

    # --- NEW: persist per-record results before y_test/y_score go out of scope ---
    engine = get_engine()
    write_per_record_results(engine, y_test, y_score)

    # ROC curve figure -- a direct report figure
    plt.figure(figsize=(6, 6))
    plt.plot(fpr, tpr, label=f"AUC = {auc:.4f}")
    plt.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Random baseline")
    plt.xlabel("False Positive Rate (FAR)")
    plt.ylabel("True Positive Rate (1 - FRR)")
    plt.title("Face Matching ROC Curve (LFW test set)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(ROC_FIGURE_PATH, dpi=150)
    plt.close()
    print(f"\nSaved ROC curve to '{ROC_FIGURE_PATH}'")

    # MLflow logging
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
    with mlflow.start_run(run_name="face_matching_lfw"):
        mlflow.log_param("dataset", "LFW (Labeled Faces in the Wild)")
        mlflow.log_param("n_train_pairs", len(y_train))
        mlflow.log_param("n_test_pairs", len(y_test))
        mlflow.log_param("pca_components", 150)
        mlflow.log_metric("auc", auc)
        for row in far_frr_table:
            mlflow.log_metric(f"far_at_{row['threshold']}", row["far"])
            mlflow.log_metric(f"frr_at_{row['threshold']}", row["frr"])
        mlflow.log_artifact(ROC_FIGURE_PATH)
        mlflow.sklearn.log_model(clf, "model")

    import joblib
    joblib.dump({"model": clf, "pca": pca}, MODEL_OUTPUT_PATH)
    print(f"Saved model + PCA transform to '{MODEL_OUTPUT_PATH}'")

    try:
        log_provenance(
            engine,
            script_name="biometric_face_matching.py",
            source_dataset="LFW (Labeled Faces in the Wild, via sklearn)",
            target_table=f"face_match_model.pkl, {RESULTS_TABLE}",
            row_count=len(y_train) + len(y_test),
            notes=f"AUC={auc:.4f}",
        )
    except Exception as e:
        print(f"(non-fatal) could not log provenance: {e}")

    print("\n" + "=" * 65)
    print("FACE MATCHING VALIDATION -- SUMMARY")
    print("=" * 65)
    print(f"AUC: {auc:.4f}")
    print("This validates the face-matching sub-component independently")
    print("of the BAF dataset -- report this alongside (not merged into)")
    print("your behavioral anomaly detection results.")
    print("=" * 65)


if __name__ == "__main__":
    main()
