"""Inspection pipeline: read the 2D code, then verify thread presence, then GO / NO-GO."""
from __future__ import annotations

import base64
from dataclasses import dataclass

import cv2
import numpy as np

from src.inspection.matrix_reader import DecodeError, decode_array
from src.inspection.thread_classifier import DetectionParams, ThreadClassifier, ThreadDecision, crop_roi

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


def _fit_scale(text: str, font: int, thickness: int, max_w: int, max_h: int) -> float:
    """Largest font scale whose rendered text fits inside max_w x max_h."""
    (w1, h1), base1 = cv2.getTextSize(text, font, 1.0, thickness)
    return max(0.5, min(max_w / max(w1, 1), max_h / max(h1 + base1, 1)))


def annotate_code(image: np.ndarray, roi, outcome: CodeOutcome) -> np.ndarray:
    """Full-frame annotated image (same pixel size as input so the UI can overlay ROIs), with the decoded
    number in a large full-width banner (green = read, red = not read) that stays legible when the image
    is shown small on the operator screen or in the PDF."""
    vis = _ensure_bgr(image).copy()
    h, w = vis.shape[:2]
    colour = GREEN if outcome.passed else RED
    box = outcome.roi if outcome.passed and outcome.roi else roi
    if box and tuple(box) != (0, 0, w, h):
        x, y, bw, bh = box
        cv2.rectangle(vis, (x, y), (x + bw, y + bh), colour, max(3, w // 320))
    label = outcome.text if outcome.passed else "2D CODE NOT READ"
    font, thickness = cv2.FONT_HERSHEY_DUPLEX, max(2, w // 400)
    band_h = max(40, int(h * 0.14))                       # ~100 px on a 1280x720 frame
    pad = int(band_h * 0.18)
    scale = min(_fit_scale(label, font, thickness, int(w * 0.96), band_h - 2 * pad), 3.2)
    (tw, th), base = cv2.getTextSize(label, font, scale, thickness)
    top = h - band_h
    overlay = vis.copy()
    cv2.rectangle(overlay, (0, top), (w, h), colour, -1)
    vis = cv2.addWeighted(overlay, 0.88, vis, 0.12, 0)    # solid, slightly translucent banner
    tx = (w - tw) // 2
    ty = top + (band_h + th) // 2 - base // 2
    cv2.putText(vis, label, (tx + 2, ty + 2), font, scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
    cv2.putText(vis, label, (tx, ty), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)
    return vis


def annotate_thread(image: np.ndarray, roi, decision: ThreadDecision) -> np.ndarray:
    """Clean result image: the camera frame as captured, with only the ROI box (if one is set)
    coloured by the verdict. No segmentation mask or markers are drawn on the operator image."""
    vis = _ensure_bgr(image).copy()
    if roi:
        x, y, w, h = roi
        colour = GREEN if decision.passed else (AMBER if decision.state == "NOT_VISIBLE" else RED)
        cv2.rectangle(vis, (x, y), (x + w, y + h), colour, 3)
    return vis


@dataclass
class CodeStep:
    result: dict          # code_result payload
    decoded_number: str | None
    image: np.ndarray | None  # annotated full frame (None when the 2D reader is bypassed)


@dataclass
class ThreadStep:
    result: dict          # thread_result payload
    passed: bool
    image: np.ndarray


MANUAL_FORMAT = "Job number (manual)"


def manual_code_step(job_number: str) -> CodeStep:
    """2D reader bypassed: the operator-entered job number stands in for the decoded part number."""
    return CodeStep(
        result={"passed": True, "text": job_number, "format": MANUAL_FORMAT,
                "message": "2D reader bypassed — job number entered by operator"},
        decoded_number=job_number,
        image=None,
    )


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
