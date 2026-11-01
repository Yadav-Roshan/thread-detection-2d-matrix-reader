"""Thread PRESENT / ABSENT / NOT_VISIBLE decision.

Fail-safe design: PRESENT requires *all* of
  1. novelty gate   – the image looks like a borescope bore view the model was trained on
                      (rejects hands, gloves, random scenes);
  2. visibility gate – enough thread-ridge evidence is resolved (rejects views from too far,
                      defocus, partial obstruction);
  3. classifier     – SVM on appearance + ridge features says Thread with margin.
Anything else is a NO-GO, reported as ABSENT (bore visible, no thread) or NOT_VISIBLE
(bore/thread cannot be seen — operator must reposition).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import cv2
import joblib
import numpy as np
from sklearn.covariance import LedoitWolf
from sklearn.decomposition import PCA
from sklearn.metrics import confusion_matrix
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from src.inspection.thread_evidence import ThreadEvidence, measure_thread_evidence

LABELS = ("No-Thread", "Thread")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
MODEL_VERSION = 2


@dataclass
class DetectionParams:
    """Operator-tunable gates (SETUP → Detection). Defaults are calibrated on the dataset."""
    min_ridge_area: float = 75000.0      # native px² of tangential thread ridges
    min_ridge_band_px: float = 120.0     # radial depth of the ridge zone (far view → small); noisy, so a floor only
    min_purity: float = 0.40             # share of fine texture that is thread-ordered
    min_classifier_margin: float = 0.25  # SVM decision value needed for PRESENT
    max_novelty: float = 2.0             # 1.0 = p99.5 of training views; genuine new sessions reach ~1.7, hands > 4.5

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "DetectionParams":
        base = asdict(cls())
        for key, value in (data or {}).items():
            if key in base:
                base[key] = float(value)
        return cls(**base)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ThreadDecision:
    state: str                   # PRESENT | ABSENT | NOT_VISIBLE
    passed: bool
    confidence: float
    message: str
    evidence: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return "Thread" if self.state == "PRESENT" else "No-Thread"


def appearance_features(image: np.ndarray) -> np.ndarray:
    """Compact, illumination-normalised global appearance descriptor."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude, angle = cv2.cartToPolar(gx, gy)
    hog: list[float] = []
    for row in range(0, 36, 12):
        for col in range(0, 64, 16):
            hist, _ = np.histogram(angle[row:row + 12, col:col + 16], bins=8, range=(0, 2 * np.pi),
                                   weights=magnitude[row:row + 12, col:col + 16])
            hog.extend((hist / (np.linalg.norm(hist) + 1e-6)).tolist())
    color = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    hsv = cv2.cvtColor(cv2.resize(color, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2HSV)
    sat_hist = cv2.calcHist([hsv], [1], None, [8], [0, 256]).ravel()
    val_hist = cv2.calcHist([hsv], [2], None, [8], [0, 256]).ravel()
    hist = np.concatenate((sat_hist, val_hist)).astype(np.float32)
    hist /= hist.sum() / 2 + 1e-6
    stats = np.array([gray.mean(), gray.std(), *np.percentile(gray, [5, 50, 95]),
                      float(magnitude.mean()), float(hsv[..., 1].mean() / 255.0)], dtype=np.float32)
    return np.concatenate((np.asarray(hog, np.float32), hist, stats))


def full_features(image: np.ndarray, evidence: ThreadEvidence | None = None) -> tuple[np.ndarray, ThreadEvidence]:
    evidence = evidence or measure_thread_evidence(image)
    return np.concatenate((appearance_features(image), evidence.vector())), evidence


def crop_roi(image: np.ndarray, roi: tuple[int, int, int, int] | None) -> np.ndarray:
    if roi is None:
        return image
    x, y, width, height = (int(v) for v in roi)
    img_h, img_w = image.shape[:2]
    if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > img_w or y + height > img_h:
        raise ValueError(f"ROI {tuple(roi)} is outside image bounds {(img_w, img_h)}")
    return image[y:y + height, x:x + width]


class _Novelty:
    """Mahalanobis distance in a PCA subspace of in-distribution features, scaled so 1.0 ≈ p99.5."""

    def __init__(self, features: np.ndarray):
        self.scaler = StandardScaler().fit(features)
        z = self.scaler.transform(features)
        self.pca = PCA(n_components=min(12, z.shape[0] - 1, z.shape[1]), random_state=0).fit(z)
        p = self.pca.transform(z)
        self.cov = LedoitWolf().fit(p)
        raw = self._raw(features)
        self.scale = float(np.percentile(raw, 99.5)) or 1.0

    def _raw(self, features: np.ndarray) -> np.ndarray:
        p = self.pca.transform(self.scaler.transform(features))
        recon = self.pca.inverse_transform(p)
        residual = np.linalg.norm(self.scaler.transform(features) - recon, axis=1) / np.sqrt(features.shape[1])
        return np.sqrt(self.cov.mahalanobis(p)) / np.sqrt(p.shape[1]) + residual

    def score(self, features: np.ndarray) -> np.ndarray:
        return self._raw(features) / self.scale


def _decide(margin: float, novelty: float, ev: ThreadEvidence, params: DetectionParams) -> tuple[str, str]:
    if novelty > params.max_novelty:
        return "NOT_VISIBLE", "View not recognised — remove any obstruction, insert the borescope into the hole and centre it"
    if ev.symmetry < 2.6 or ev.coverage < 0.6:
        return "NOT_VISIBLE", "Bore not found in view — re-centre the borescope on the hole"
    resolved = ev.ridge_area >= params.min_ridge_area and ev.band_px >= params.min_ridge_band_px
    if not resolved and ev.purity >= params.min_purity:
        return "NOT_VISIBLE", "Thread rings too small to verify — move the borescope closer / into the hole"
    if resolved and ev.purity >= params.min_purity and margin >= params.min_classifier_margin:
        return "PRESENT", "Thread ridges detected around the bore"
    return "ABSENT", "No thread detected — if the borescope is not inserted and in focus, reposition and CHECK again"


class ThreadClassifier:
    def __init__(self, model: Any, novelty: _Novelty, metrics: Mapping[str, Any] | None = None):
        self._model = model
        self._novelty = novelty
        self.metrics = dict(metrics or {})

    # ---------------------------------------------------------------- training
    @staticmethod
    def load_dataset(root: str | Path) -> tuple[list[np.ndarray], np.ndarray, list[str]]:
        images, labels, names = [], [], []
        for label in LABELS:
            folder = Path(root) / label
            if not folder.is_dir():
                raise FileNotFoundError(f"Missing dataset folder: {folder}")
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError(f"Could not read training image: {path}")
                images.append(image)
                labels.append(label)
                names.append(str(path))
        y = np.asarray(labels)
        if len(set(labels)) != 2 or np.unique(y, return_counts=True)[1].min() < 2:
            raise ValueError("Dataset needs at least two Thread and two No-Thread images")
        return images, y, names

    @staticmethod
    def _make_svm():
        return make_pipeline(StandardScaler(), SVC(kernel="rbf", C=3.0, gamma="scale", class_weight="balanced"))

    @classmethod
    def fit_features(cls, x: np.ndarray, y: np.ndarray, metrics: Mapping[str, Any] | None = None) -> "ThreadClassifier":
        model = cls._make_svm().fit(x, y)
        return cls(model, _Novelty(x), metrics)

    @classmethod
    def train(cls, dataset_root: str | Path) -> "ThreadClassifier":
        images, y, _ = cls.load_dataset(dataset_root)
        feats = [full_features(image) for image in images]
        x = np.vstack([f for f, _ in feats])
        evidences = [e for _, e in feats]
        metrics = cls.cross_validate(x, y, evidences)
        return cls.fit_features(x, y, metrics)

    @classmethod
    def cross_validate(cls, x: np.ndarray, y: np.ndarray, evidences: list[ThreadEvidence],
                       params: DetectionParams | None = None) -> dict:
        """Stratified 5-fold CV of the complete gated decision (not just the SVM)."""
        params = params or DetectionParams()
        folds = min(5, int(np.unique(y, return_counts=True)[1].min()))
        splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=42)
        predicted = np.empty(len(y), dtype=object)
        for train_idx, test_idx in splitter.split(x, y):
            fold = cls.fit_features(x[train_idx], y[train_idx])
            for i in test_idx:
                decision = fold._decision(x[i:i + 1], evidences[i], params)
                predicted[i] = decision.label
        matrix = confusion_matrix(y, predicted.astype(str), labels=list(LABELS))
        tn, fp, fn, tp = matrix.ravel()
        return {
            "model_version": MODEL_VERSION,
            "sample_count": int(len(y)),
            "labels": list(LABELS),
            "folds": folds,
            "confusion_matrix": matrix.tolist(),
            "accuracy": float((tp + tn) / len(y)),
            "thread_recall": float(tp / max(tp + fn, 1)),
            "false_accept_rate": float(fp / max(fp + tn, 1)),
        }

    # ---------------------------------------------------------------- inference
    def _decision(self, x: np.ndarray, evidence: ThreadEvidence, params: DetectionParams) -> ThreadDecision:
        margin = float(np.asarray(self._model.decision_function(x)).reshape(-1)[0])
        # decision_function is positive for classes_[1]
        if str(self._model.classes_[1]) != "Thread":
            margin = -margin
        novelty = float(self._novelty.score(x)[0])
        state, message = _decide(margin, novelty, evidence, params)
        if state == "PRESENT":
            confidence = 1.0 / (1.0 + np.exp(-2.0 * margin))
        else:
            confidence = 1.0 / (1.0 + np.exp(2.0 * max(margin, -3.0))) if state == "ABSENT" else min(1.0, 0.5 + 0.5 * min(novelty, 1.0))
        evidence_dict = {
            "ridge_area": round(evidence.ridge_area),
            "ridge_band_px": round(evidence.band_px, 1),
            "purity": round(evidence.purity, 3),
            "symmetry": round(evidence.symmetry, 2),
            "coverage": round(evidence.coverage, 2),
            "classifier_margin": round(margin, 3),
            "novelty": round(novelty, 3),
            "center": [round(evidence.center_x, 1), round(evidence.center_y, 1)],
        }
        return ThreadDecision(state=state, passed=state == "PRESENT", confidence=float(confidence),
                              message=message, evidence=evidence_dict)

    def predict(self, image: np.ndarray, roi: tuple[int, int, int, int] | None = None,
                params: DetectionParams | None = None) -> ThreadDecision:
        cropped = crop_roi(image, roi)
        x, evidence = full_features(cropped)
        return self._decision(x.reshape(1, -1), evidence, params or DetectionParams())

    # ---------------------------------------------------------------- persistence
    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"version": MODEL_VERSION, "model": self._model, "novelty": self._novelty,
                     "metrics": self.metrics}, destination)
        return destination

    @classmethod
    def load(cls, path: str | Path) -> "ThreadClassifier":
        payload = joblib.load(path)
        if not isinstance(payload, dict) or payload.get("version") != MODEL_VERSION:
            raise ValueError(f"Model at {path} is missing or from an older version; retrain")
        return cls(payload["model"], payload["novelty"], payload.get("metrics", {}))
