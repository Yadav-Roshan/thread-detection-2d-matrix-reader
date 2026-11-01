"""Two-role camera manager: the 2D-code camera and the thread borescope."""
from __future__ import annotations

import threading
import time
from typing import Literal, Optional

import cv2

from src.cameras.webcam import WebcamCamera, open_capture
from src.core import config

Role = Literal["code", "thread"]
ROLES: tuple[Role, ...] = ("code", "thread")


class CameraManager:
    def __init__(self) -> None:
        self._cameras: dict[str, Optional[WebcamCamera]] = {role: None for role in ROLES}
        self._lock = threading.RLock()
        self._discovery_cache: tuple[float, list[dict]] | None = None

    def active(self) -> dict[str, Optional[int]]:
        with self._lock:
            return {role: (cam.index if cam else None) for role, cam in self._cameras.items()}

    def get(self, role: str) -> Optional[WebcamCamera]:
        with self._lock:
            return self._cameras.get(role)

    def discover(self, max_age: float = 15.0) -> list[dict]:
        """Probe device indices (cached briefly). Indices opened by this app are not reopened."""
        cached = self._discovery_cache
        if cached and time.monotonic() - cached[0] < max_age:
            return cached[1]
        devices = self._probe()
        self._discovery_cache = (time.monotonic(), devices)
        return devices

    def _probe(self) -> list[dict]:
        with self._lock:
            in_use = {cam.index: cam for cam in self._cameras.values() if cam}
        devices: list[dict] = []
        for index in range(config.CAMERA_PROBE_MAX):
            if index in in_use:
                width, height = in_use[index].size()
                devices.append(_device(index, width, height))
                continue
            cap = open_capture(index)
            try:
                if not cap.isOpened():
                    continue
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                devices.append(_device(index, frame.shape[1], frame.shape[0]))
            finally:
                cap.release()
        return devices

    def select(self, role: str, index: int) -> tuple[bool, str]:
        if role not in ROLES:
            raise ValueError(f"Unknown camera role '{role}'")
        with self._lock:
            other = next((r for r, cam in self._cameras.items() if cam and cam.index == index and r != role), None)
            if other:
                return False, f"Camera {index} is already used as the {other} camera"
            current = self._cameras.get(role)
            if current and current.index == index and current.is_connected():
                return True, f"Camera {index} already active"
            if current:
                current.disconnect()
                self._cameras[role] = None
            cam = WebcamCamera(index)
            if not cam.connect():
                return False, f"Could not open camera {index}. Check the cable and close other apps using it."
            self._cameras[role] = cam
            return True, f"Camera {index} connected"

    def release(self, role: str) -> None:
        with self._lock:
            cam = self._cameras.get(role)
            if cam:
                cam.disconnect()
            self._cameras[role] = None

    def frame(self, role: str):
        cam = self.get(role)
        return cam.get_frame() if cam else None

    def averaged_frame(self, role: str, count: int = 3):
        cam = self.get(role)
        return cam.get_averaged_frame(count) if cam else None

    def shutdown(self) -> None:
        for role in ROLES:
            self.release(role)


def _device(index: int, width: int, height: int) -> dict:
    resolution = f"{width}×{height}" if width and height else "unknown resolution"
    return {"index": index, "label": f"Camera {index} ({resolution})", "width": width, "height": height}


def encode_jpeg(frame, quality: int = 80) -> bytes:
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buffer.tobytes()
