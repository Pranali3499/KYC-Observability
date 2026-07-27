"""
biometric_liveness_detection.py
Stage 4 -- Biometric Validation: Liveness Detection
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Validates the liveness sub-component of Layer 5 using a real-vs-fake
face image dataset (e.g. Kaggle "Real and Fake Face Detection").

IMPORTANT -- same principle as biometric_face_matching.py: this is a
SEPARATE, standalone validation. There is no applicant-level link
between these face images and your BAF onboarding records, so nothing
here gets joined into behavioral_features. This validates whether a
liveness-detection approach CAN distinguish real vs. spoofed faces,
using data appropriate for that question.

Approach: Local Binary Pattern (LBP) texture features + Random Forest.
This is a classical, well-established liveness-detection baseline
from the pre-deep-learning literature (spoofed/printed/screen-replayed
faces have different micro-texture statistics than real skin) -- a
legitimate, lightweight PoC baseline that doesn't require GPU/TensorFlow.

EXPECTED FOLDER LAYOUT (standard for most Kaggle real/fake face sets):
    <data_dir>/
        real/
            image001.jpg
            image002.jpg
            ...
        fake/
            image001.jpg
            ...

If your downloaded dataset uses different subfolder names (e.g.
"training_real"/"training_fake"), pass them explicitly:
    python biometric_liveness_detection.py --data-dir path/to/data --real-dir training_real --fake-dir training_fake

Usage:
    python biometric_liveness_detection.py --data-dir path/to/dataset

Requires:
    pip install scikit-image scikit-learn mlflow matplotlib pillow
"""

import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import numpy as np
from PIL import Image
from skimage.feature import local_binary_pattern
from skimage.color import rgb2gray
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.model_selection import train_test_split

from provenance import log_provenance
from db_config import get_engine

MLFLOW_EXPERIMENT_NAME = "kyc-biometric-validation"
MODEL_OUTPUT_PATH = "liveness_model.pkl"
ROC_FIGURE_PATH = "liveness_roc_curve.png"

IMAGE_SIZE = (128, 128)
LBP_RADIUS = 3
LBP_N_POINTS = 8 * LBP_RADIUS

THRESHOLDS_TO_REPORT = [0.3, 0.4, 0.5, 0.6, 0.7]


def load_image_paths(data_dir: str, real_dir: str, fake_dir: str) -> tuple[list, list]:
    real_path = os.path.join(data_dir, real_dir)
    fake_path = os.path.join(data_dir, fake_dir)

    if not os.path.isdir(real_path) or not os.path.isdir(fake_path):
        raise FileNotFoundError(
            f"Expected subfolders not found:\n  {real_path}\n  {fake_path}\n"
            f"Check --data-dir, --real-dir, --fake-dir match your downloaded dataset's actual folder names."
        )

    valid_ext = (".jpg", ".jpeg", ".png")
    real_files = [os.path.join(real_path, f) for f in os.listdir(real_path) if f.lower().endswith(valid_ext)]
    fake_files = [os.path.join(fake_path, f) for f in os.listdir(fake_path) if f.lower().endswith(valid_ext)]

    print(f"Found {len(real_files):,} real images, {len(fake_files):,} fake images")
    return real_files, fake_files


def extract_lbp_features(image_path: str) -> np.ndarray:
    """
    Loads an image, converts to grayscale, resizes, computes a Local
    Binary Pattern texture histogram. LBP captures micro-texture
    patterns -- real skin has different texture statistics than a
    printed photo or screen replay, which is the classical basis for
    texture-based liveness detection.
    """
    img = Image.open(image_path).convert("RGB").resize(IMAGE_SIZE)
    gray = rgb2gray(np.array(img))
    lbp = local_binary_pattern(gray, LBP_N_POINTS, LBP_RADIUS, method="uniform")
    n_bins = LBP_N_POINTS + 2
    hist, _ = np.histogram(lbp, bins=n_bins, range=(0, n_bins), density=True)
    return hist


def build_dataset(real_files: list, fake_files: list) -> tuple[np.ndarray, np.ndarray]:
    print("Extracting LBP texture features...")
    features, labels = [], []
    for path in real_files:
        try:
            features.append(extract_lbp_features(path))
            labels.append(1)  # 1 = real/live
        except Exception as e:
            print(f"  (skipped {path}: {e})")
    for path in fake_files:
        try:
            features.append(extract_lbp_features(path))
            labels.append(0)  # 0 = fake/spoof
        except Exception as e:
            print(f"  (skipped {path}: {e})")
    return np.array(features), np.array(labels)


def far_frr_at_threshold(y_true: np.ndarray, y_score: np.ndarray, threshold: float) -> tuple[float, float]:
    """FAR = fake images wrongly accepted as real. FRR = real images wrongly rejected as fake."""
    y_pred = (y_score >= threshold).astype(int)
    fake_mask = y_true == 0
    real_mask = y_true == 1
    far = (y_pred[fake_mask] == 1).mean() if fake_mask.any() else float("nan")
    frr = (y_pred[real_mask] == 0).mean() if real_mask.any() else float("nan")
    return far, frr


def main():
    parser = argparse.ArgumentParser(description="Stage 4: Liveness detection validation")
    parser.add_argument("--data-dir", required=True, help="Path to dataset root folder")
    parser.add_argument("--real-dir", default="real", help="Subfolder name for real images (default: real)")
    parser.add_argument("--fake-dir", default="fake", help="Subfolder name for fake images (default: fake)")
    args = parser.parse_args()

    print("=" * 65)
    print("STAGE 4 -- Biometric Validation: Liveness Detection")
    print("Behavioral Observability Framework for KYC Onboarding")
    print("=" * 65)

    real_files, fake_files = load_image_paths(args.data_dir, args.real_dir, args.fake_dir)
    X, y = build_dataset(real_files, fake_files)
    print(f"Feature matrix: {X.shape}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    print("\nTraining liveness classifier (Random Forest on LBP features)...")
    clf = RandomForestClassifier(n_estimators=200, random_state=42, n_jobs=-1)
    clf.fit(X_train, y_train)

    y_score = clf.predict_proba(X_test)[:, 1]  # P(real/live)
    auc = roc_auc_score(y_test, y_score)
    fpr, tpr, _ = roc_curve(y_test, y_score)

    print(f"\nTest set AUC: {auc:.4f}")

    print("\nFAR / FRR at several thresholds:")
    print(f"{'Threshold':<12}{'FAR':<10}{'FRR':<10}")
    far_frr_table = []
    for t in THRESHOLDS_TO_REPORT:
        far, frr = far_frr_at_threshold(y_test, y_score, t)
        print(f"{t:<12}{far:<10.4f}{frr:<10.4f}")
        far_frr_table.append({"threshold": t, "far": far, "frr": frr})

    plt.figure(figsize=(6, 6))
    plt.plot(fpr, tpr, label=f"AUC = {auc:.4f}")
    plt.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Random baseline")
    plt.xlabel("False Positive Rate (FAR)")
    plt.ylabel("True Positive Rate (1 - FRR)")
    plt.title("Liveness Detection ROC Curve")
    plt.legend()
    plt.tight_layout()
    plt.savefig(ROC_FIGURE_PATH, dpi=150)
    plt.close()
    print(f"\nSaved ROC curve to '{ROC_FIGURE_PATH}'")

    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
    with mlflow.start_run(run_name="liveness_detection"):
        mlflow.log_param("dataset_dir", args.data_dir)
        mlflow.log_param("n_real", len(real_files))
        mlflow.log_param("n_fake", len(fake_files))
        mlflow.log_param("feature_method", "LBP")
        mlflow.log_metric("auc", auc)
        for row in far_frr_table:
            mlflow.log_metric(f"far_at_{row['threshold']}", row["far"])
            mlflow.log_metric(f"frr_at_{row['threshold']}", row["frr"])
        mlflow.log_artifact(ROC_FIGURE_PATH)
        mlflow.sklearn.log_model(clf, "model")

    import joblib
    joblib.dump(clf, MODEL_OUTPUT_PATH)
    print(f"Saved model to '{MODEL_OUTPUT_PATH}'")

    try:
        engine = get_engine()
        log_provenance(
            engine,
            script_name="biometric_liveness_detection.py",
            source_dataset=args.data_dir,
            target_table="liveness_model.pkl",
            row_count=len(y),
            notes=f"AUC={auc:.4f}",
        )
    except Exception as e:
        print(f"(non-fatal) could not log provenance: {e}")

    print("\n" + "=" * 65)
    print("LIVENESS DETECTION VALIDATION -- SUMMARY")
    print("=" * 65)
    print(f"AUC: {auc:.4f}")
    print("=" * 65)


if __name__ == "__main__":
    main()
