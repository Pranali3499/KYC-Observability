# Go/No-Go Checklist — Biometric Validation
## Behavioral Observability Framework for KYC Onboarding
Pranali Pandharinath Supekar (2024DA04387)

Purpose: a documented decision gate for whether the biometric layer
(Layer 5) is validated enough to report as functioning, per the
evaluator's requirement for a "go/no-go checklist for biometric
validation completion." This is a PoC-scope dissertation checklist,
not a production security sign-off.

Assessed against: `biometric_face_matching.py`, `biometric_liveness_detection.py`

---

## Face Matching Sub-Component

| Criterion | Target | Actual | Status |
|---|---|---|---|
| Model trained on appropriate validation data | Face-pair dataset with genuine/impostor labels | LFW (Labeled Faces in the Wild) | ✅ PASS |
| AUC exceeds random baseline (0.5) | > 0.5 | 0.6940 | ✅ PASS |
| FAR/FRR tradeoff characterized at multiple thresholds | Report FAR/FRR across a threshold range | 5 thresholds reported (0.3–0.7) | ✅ PASS |
| Results logged for auditability (MLflow) | Yes | Yes — `kyc-biometric-validation` experiment | ✅ PASS |
| Cross-dataset generalization tested | At least one dataset beyond training | Not yet — LFW train/test split only | ⚠️ PARTIAL |
| Production-grade accuracy (deep embeddings) | AUC approaching >0.95 | 0.6940 (classical PCA+LogReg baseline) | ❌ NOT MET |

**Decision: CONDITIONAL GO** — validated as a working proof-of-concept
with real, defensible metrics. NOT sufficient for production deployment
without upgrading to a deep embedding model (FaceNet/ArcFace), which is
explicitly scoped as future work.

---

## Liveness Detection Sub-Component

| Criterion | Target | Actual | Status |
|---|---|---|---|
| Model trained on appropriate validation data | Real vs. spoof/fake face dataset | Kaggle "Real and Fake Face Detection" (1,081 real + 960 fake) | ✅ PASS |
| AUC exceeds random baseline (0.5) | > 0.5 | 0.5228 | ⚠️ MARGINAL |
| FAR/FRR tradeoff characterized | Report FAR/FRR across a threshold range | 5 thresholds reported | ✅ PASS |
| Results logged for auditability (MLflow) | Yes | Yes — `kyc-biometric-validation` experiment | ✅ PASS |
| Feature approach appropriate for spoof type in dataset | LBP texture features suited to print/replay spoofs | Dataset likely contains GAN/deepfake-style fakes, not physical spoofs | ❌ MISMATCH IDENTIFIED |

**Decision: NO-GO for reported effectiveness, GO for demonstrating the
validation methodology.** AUC of 0.5228 is not meaningfully better than
random guessing — this sub-component does NOT currently provide reliable
liveness detection. The validation pipeline itself (data loading, feature
extraction, model training, FAR/FRR reporting, MLflow logging) is sound
and reusable; the modeling APPROACH needs to change.

**Recommended remediation (future work, out of scope for this
dissertation phase):** replace LBP + Random Forest with a CNN-based
liveness model, which is better suited to detecting GAN-generated/
deepfake artifacts than classical texture features.

---

## Overall Biometric Layer Go/No-Go

| Component | Status | Suitable for dissertation reporting? |
|---|---|---|
| Face matching | Conditional GO | Yes — report AUC 0.694 as a valid PoC baseline, note deep-embedding upgrade path |
| Liveness detection | No-Go (effectiveness) / Go (methodology) | Yes — report AUC 0.523 as an honest negative result with root-cause analysis, not hidden |
| Combined biometric layer | **NOT production-ready** | Correctly scoped and labeled as PoC throughout the codebase (see `blend_biometric()` docstring warning against using synthetic scores as evidence) |

**Overall assessment:** the biometric layer's validation *process* meets
the dissertation's requirements (real datasets, real metrics, real
FAR/FRR analysis, logged for auditability). The face-matching *result*
is a legitimate, reportable PoC finding. The liveness *result* is a
genuine limitation, reported honestly with a plausible explanation —
this is acceptable and expected at PoC/dissertation scope, where the
goal is demonstrating a sound methodology, not shipping a production
biometric system.

**Sign-off:** This checklist should be reviewed against final report
content before submission to ensure claims about biometric layer
performance match what is documented here — i.e., face matching is
presented as a working baseline with a clear upgrade path, and liveness
detection is presented as a validated-but-currently-ineffective
approach with a specific, technically justified explanation.
