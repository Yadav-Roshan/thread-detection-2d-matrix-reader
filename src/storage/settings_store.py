"""JSON-backed station settings: ROIs, detection gates, SETUP password hash."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from src.core import config
from src.inspection.thread_classifier import DetectionParams

_lock = threading.Lock()
ROI_KEYS = ("x", "y", "width", "height")


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def normalize_roi(value: Any) -> dict | None:
    """None / missing / zero-size → None (use full frame). Otherwise a validated int ROI."""
    if not value:
        return None
    try:
        roi = {key: int(round(float(value[key]))) for key in ROI_KEYS}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ROI needs numeric x, y, width, height") from exc
    if roi["width"] <= 0 or roi["height"] <= 0:
        return None
    if roi["x"] < 0 or roi["y"] < 0:
        raise ValueError("ROI x and y must be ≥ 0")
    return roi


def roi_tuple(roi: dict | None) -> tuple[int, int, int, int] | None:
    return None if roi is None else tuple(roi[key] for key in ROI_KEYS)  # type: ignore[return-value]


def clamp_roi(roi: dict | None, width: int, height: int) -> tuple[int, int, int, int] | None:
    """Clip a saved ROI to the actual frame; fall back to full frame if nothing remains."""
    if roi is None:
        return None
    x, y = min(roi["x"], width - 1), min(roi["y"], height - 1)
    w, h = min(roi["width"], width - x), min(roi["height"], height - y)
    if w < 16 or h < 16:
        return None
    return (x, y, w, h)


class SettingsStore:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory or config.CONFIG_DIR)

    @property
    def _rois_path(self) -> Path:
        return self.dir / "rois.json"

    @property
    def _detection_path(self) -> Path:
        return self.dir / "detection.json"

    @property
    def _auth_path(self) -> Path:
        return self.dir / "auth.json"

    # ROIs
    def get_rois(self) -> dict:
        data = _read(self._rois_path, {})
        return {"code": normalize_roi(data.get("code")), "thread": normalize_roi(data.get("thread"))}

    def set_rois(self, data: dict) -> dict:
        rois = {"code": normalize_roi(data.get("code")), "thread": normalize_roi(data.get("thread"))}
        with _lock:
            _write(self._rois_path, rois)
        return rois

    # Camera assignment (reopened at startup)
    def get_cameras(self) -> dict:
        data = _read(self.dir / "cameras.json", {})
        return {role: (int(data[role]) if isinstance(data.get(role), int) else None) for role in ("code", "thread")}

    def set_cameras(self, active: dict) -> None:
        with _lock:
            _write(self.dir / "cameras.json", {role: active.get(role) for role in ("code", "thread")})

    # Report directory (PDFs: <dir>/<YYYY>/<MM>/<DD>/<GO|NO_GO>/...)
    @staticmethod
    def _validate_directory(directory: str | None, default: Path, what: str) -> tuple[Path, bool]:
        text = (directory or "").strip().strip('"')
        path = Path(text).expanduser() if text else Path(default)
        if text and not path.is_absolute():
            raise ValueError(f"{what} must be an absolute path, e.g. D:/Reports")
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise ValueError(f"Cannot write to {path}: {exc.strerror or exc}") from exc
        return path, bool(text)

    def get_report_directory(self) -> Path:
        value = _read(self.dir / "report_config.json", {}).get("output_directory")
        return Path(value) if value else Path(config.PDF_DIR)

    def set_report_directory(self, directory: str | None) -> Path:
        path, custom = self._validate_directory(directory, config.PDF_DIR, "Report directory")
        with _lock:
            data = _read(self.dir / "report_config.json", {})
            data["output_directory"] = str(path) if custom else None
            _write(self.dir / "report_config.json", data)
        return path

    # Result images folder (latest code_result.jpg + thread_result.jpg, overwritten every run)
    def get_result_images_directory(self) -> Path:
        value = _read(self.dir / "report_config.json", {}).get("result_images_directory")
        return Path(value) if value else Path(config.RESULT_IMAGES_DIR)

    def set_result_images_directory(self, directory: str | None) -> Path:
        path, custom = self._validate_directory(directory, config.RESULT_IMAGES_DIR, "Result images folder")
        with _lock:
            data = _read(self.dir / "report_config.json", {})
            data["result_images_directory"] = str(path) if custom else None
            _write(self.dir / "report_config.json", data)
        return path

    # Hercules link
    def get_hercules(self) -> dict:
        from src.outputs.hercules_server import normalize_config
        try:
            return normalize_config(_read(self.dir / "hercules.json", {}))
        except ValueError:
            return normalize_config({})

    def set_hercules(self, data: dict) -> dict:
        from src.outputs.hercules_server import normalize_config
        cfg = normalize_config(data)
        with _lock:
            _write(self.dir / "hercules.json", cfg)
        return cfg

    # Detection gates
    def get_detection(self) -> DetectionParams:
        return DetectionParams.from_mapping(_read(self._detection_path, {}))

    def set_detection(self, data: dict) -> DetectionParams:
        unknown = set(data) - set(DetectionParams().as_dict())
        if unknown:
            raise ValueError(f"Unknown detection parameter(s): {', '.join(sorted(unknown))}")
        params = DetectionParams.from_mapping({**self.get_detection().as_dict(), **data})
        values = params.as_dict()
        if any(v < 0 for v in values.values()):
            raise ValueError("Detection parameters must be non-negative")
        if values["min_purity"] > 1:
            raise ValueError("min_purity must be between 0 and 1")
        with _lock:
            _write(self._detection_path, values)
        return params

    # Auth (sha256, same scheme as cut-mark-station)
    def _hash(self) -> str:
        return _read(self._auth_path, {}).get("password_hash") or config.DEFAULT_PASSWORD_HASH

    def check_password(self, password: str) -> bool:
        return hashlib.sha256(password.encode()).hexdigest() == self._hash()

    def change_password(self, old: str, new: str) -> tuple[bool, str]:
        if not self.check_password(old):
            return False, "Old password is incorrect"
        if len(new) < 6:
            return False, "New password must be at least 6 characters"
        with _lock:
            _write(self._auth_path, {"password_hash": hashlib.sha256(new.encode()).hexdigest()})
        return True, "Password changed successfully"
