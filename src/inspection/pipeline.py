"""Inspection pipeline: read the 2D code, then verify thread presence, then GO / NO-GO."""
from __future__ import annotations

import base64
from dataclasses import dataclass

import cv2
import numpy as np

from src.inspection.matrix_reader import DecodeError, decode_array
from src.inspection.thread_classifier import DetectionParams, ThreadClassifier, ThreadDecision, crop_roi
from src.inspection.thread_evidence import ThreadEvidence, ridge_mask

TEAL, GREEN, RED, AMBER = (136, 148, 13), (74, 163, 22), (38, 38, 220), (11, 158, 245)


@dataclass
class CodeOutcome:
    passed: bool
    text: str | None
    format: str | None
    message: str
    roi: tuple[int, int, int, int] | None


def read_code(image: np.ndarray, roi: tuple[int, int, int, int] | None, max_seconds: float = 4.0) -> CodeOutcome:
    try:
        result = decode_array(image, roi, "capture", max_seconds=max_seconds)
    except DecodeError:
        where = "ROI" if roi else "frame"
        return CodeOutcome(False, None, None, f"2D code not readable in {where} — check focus/lighting/ROI", roi)
    return CodeOutcome(True, result.text, result.format, "Decoded", result.roi)


def _data_url(image: np.ndarray, max_width: int = 1280) -> str:
    if image.shape[1] > max_width:
        scale = max_width / image.shape[1]
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


def _ensure_bgr(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image


def annotate_code(image: np.ndarray, roi, outcome: CodeOutcome) -> np.ndarray:
    """Full-frame annotated image (same pixel size as input so the UI can overlay ROIs)."""
    vis = _ensure_bgr(image).copy()
    colour = GREEN if outcome.passed else RED
    box = outcome.roi if outcome.passed and outcome.roi else roi
    full = (0, 0, vis.shape[1], vis.shape[0])
    if box and tuple(box) != full:
        x, y, w, h = box
        cv2.rectangle(vis, (x, y), (x + w, y + h), colour, 3)
    label = outcome.text if outcome.passed else "2D CODE NOT READ"
    scale = max(0.6, vis.shape[1] / 1280 * 0.9)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    cv2.rectangle(vis, (0, vis.shape[0] - th - 24), (tw + 24, vis.shape[0]), (20, 20, 20), -1)
    cv2.putText(vis, label, (12, vis.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, 2, cv2.LINE_AA)
    return vis


def annotate_thread(image: np.ndarray, roi, decision: ThreadDecision) -> np.ndarray:
    vis = _ensure_bgr(image).copy()
    x, y, w, h = roi if roi else (0, 0, vis.shape[1], vis.shape[0])
    crop = vis[y:y + h, x:x + w]
    try:
        cx, cy = decision.evidence.get("center", [w / 2, h / 2])
        evidence = ThreadEvidence(0, 0, 0, 0, 0, float(cx), float(cy))
        mask = ridge_mask(crop, evidence)
        if decision.state != "NOT_VISIBLE":
            overlay_colour = np.array(GREEN if decision.passed else AMBER, np.uint8)
            crop[mask] = (0.45 * crop[mask] + 0.55 * overlay_colour).astype(np.uint8)
        centre = (int(evidence.center_x), int(evidence.center_y))
        if 0 <= centre[0] < w and 0 <= centre[1] < h:
            cv2.drawMarker(crop, centre, (255, 255, 255), cv2.MARKER_CROSS, 24, 2)
    except ValueError:
        pass
    colour = GREEN if decision.passed else (AMBER if decision.state == "NOT_VISIBLE" else RED)
    if roi:
        cv2.rectangle(vis, (x, y), (x + w, y + h), colour, 3)
    return vis


@dataclass
class CodeStep:
    result: dict          # code_result payload
    decoded_number: str | None
    image: np.ndarray     # annotated full frame


@dataclass
class ThreadStep:
    result: dict          # thread_result payload
    passed: bool
    image: np.ndarray


def run_code_step(frame: np.ndarray, roi) -> CodeStep:
    code = read_code(frame, roi)
    return CodeStep(
        result={"passed": code.passed, "text": code.text, "format": code.format, "message": code.message},
        decoded_number=code.text,
        image=annotate_code(frame, roi, code),
    )


def run_thread_step(frame: np.ndarray, roi, classifier: ThreadClassifier, params: DetectionParams) -> ThreadStep:
    crop_roi(frame, roi)  # validates ROI bounds early
    decision = classifier.predict(frame, roi, params)
    return ThreadStep(
        result={
            "passed": decision.passed,
            "state": decision.state,
            "confidence": round(decision.confidence, 4),
            "message": decision.message,
            "evidence": decision.evidence,
        },
        passed=decision.passed,
        image=annotate_thread(frame, roi, decision),
    )


def overall(code: CodeStep | None, thread: ThreadStep) -> str:
    return "GO" if code is not None and code.result["passed"] and thread.passed else "NO_GO"


def to_data_url(image: np.ndarray) -> str:
    return _data_url(image)
