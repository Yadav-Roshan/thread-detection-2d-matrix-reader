"""USB / UVC camera (borescope) with a background grab thread — same pattern as cut-mark-station."""
from __future__ import annotations

import sys
import threading
import time
from typing import Optional

import cv2
import numpy as np

from src.core import config


def _backend() -> int:
    # DirectShow opens UVC borescopes far more reliably than MSMF on Windows.
    return cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY


def open_capture(index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index, _backend())
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(index)
    if cap.isOpened():
        # Order matters on DirectShow: size first, then MJPG. UVC borescopes deliver
        # ~4.6 fps at 1280x720 in YUY2 but ~18 fps in MJPG.
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class WebcamCamera:
    """One capture device. The grab thread owns the device: it opens, reads, reconnects and
    releases it. DirectShow captures misbehave when opened on one thread and read on another."""

    STALE_SECONDS = 3.0   # no frame for this long -> reopen the device

    def __init__(self, index: int):
        self.index = index
        self._frame: Optional[np.ndarray] = None
        self._frame_time = 0.0
        self._frame_count = 0
        self._lock = threading.Lock()
        self._running = False
        self._opened = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.reconnects = 0

    def connect(self, timeout: float = 6.0) -> bool:
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=f"cam-{self.index}", daemon=True)
        self._thread.start()
        if not self._opened.wait(timeout=timeout / 2):
            self.disconnect()
            return False
        # Borescopes emit black frames while the sensor/LED warms up; wait for a real image
        # (or accept whatever arrives after the warm-up window, e.g. a genuinely dark scene).
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            frame = self.get_frame()
            if frame is not None and (frame.mean() > 3 or time.monotonic() - start > 2.5):
                return True
            time.sleep(0.05)
        self.disconnect()
        return False

    def _loop(self) -> None:
        cap: Optional[cv2.VideoCapture] = None
        last_ok = time.monotonic()
        try:
            while self._running:
                if cap is None:
                    cap = open_capture(self.index)
                    if not cap.isOpened():
                        cap.release()
                        cap = None
                        if not self._opened.is_set():
                            return  # initial open failed; connect() reports it
                        time.sleep(1.0)
                        continue
                    self._opened.set()
                    last_ok = time.monotonic()
                ok, frame = cap.read()
                now = time.monotonic()
                if ok and frame is not None:
                    last_ok = now
                    with self._lock:
                        self._frame = frame
                        self._frame_time = now
                        self._frame_count += 1
                elif now - last_ok > self.STALE_SECONDS:
                    # Device stalled (USB hiccup / unplug): release and reopen.
                    cap.release()
                    cap = None
                    self.reconnects += 1
                else:
                    time.sleep(0.03)
        finally:
            if cap is not None:
                cap.release()

    def get_frame(self, max_age: float = 2.0) -> Optional[np.ndarray]:
        with self._lock:
            if self._frame is None or time.monotonic() - self._frame_time > max_age:
                return None
            return self._frame.copy()

    def get_averaged_frame(self, count: int = 3, timeout: float = 1.0) -> Optional[np.ndarray]:
        """Mean of the next ``count`` fresh frames — suppresses sensor noise for repeatable decisions.
        Falls back to whatever was collected (or the latest frame) if the camera is slow."""
        frames: list[np.ndarray] = []
        with self._lock:
            seen = self._frame_count
        deadline = time.monotonic() + timeout
        while len(frames) < count and time.monotonic() < deadline:
            with self._lock:
                if self._frame_count != seen and self._frame is not None:
                    seen = self._frame_count
                    frames.append(self._frame.copy())
            time.sleep(0.01)
        if not frames:
            return self.get_frame()
        if len(frames) == 1:
            return frames[0]
        return np.mean(np.stack(frames).astype(np.float32), axis=0).round().astype(np.uint8)

    def is_connected(self) -> bool:
        return self._running and self.get_frame() is not None

    def size(self) -> tuple[int, int]:
        frame = self.get_frame()
        if frame is None:
            return (0, 0)
        return (frame.shape[1], frame.shape[0])

    def disconnect(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            self._frame = None
