"""Runtime configuration (paths + environment), mirroring cut-mark-station's src/core/config."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Frozen (PyInstaller onedir): read-only resources live in the bundle (_internal),
# writable station data lives next to the .exe — same split as cut-mark-station.
FROZEN = bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))
if FROZEN:
    RESOURCE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = BASE_DIR = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    """Optional KEY=VALUE overrides in .env beside the exe / project root."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_env_file(BASE_DIR / ".env")


def _path(env: str, default: Path) -> Path:
    value = os.getenv(env)
    return Path(value) if value else default


DATA_DIR = _path("THREAD_DATA_DIR", BASE_DIR / "data")          # training images (for retraining)
CONFIG_DIR = _path("THREAD_CONFIG_DIR", BASE_DIR / "config")
_BUNDLED_MODEL = RESOURCE_DIR / "models" / "thread_classifier.joblib"
_LOCAL_MODEL = BASE_DIR / "models" / "thread_classifier.joblib"
# A retrained model beside the exe wins over the bundled one.
MODEL_PATH = _path("THREAD_MODEL_PATH", _LOCAL_MODEL if (not FROZEN or _LOCAL_MODEL.exists()) else _BUNDLED_MODEL)
MODEL_SAVE_PATH = _path("THREAD_MODEL_PATH", _LOCAL_MODEL)
PDF_DIR = _path("THREAD_PDF_DIR", BASE_DIR / "reports" / "pdf")
ASSETS_DIR = RESOURCE_DIR / "assets"
FRAMES_DIR = _path("THREAD_FRAMES_DIR", BASE_DIR / "frames")
# Latest annotated result image per camera, overwritten on every run.
RESULT_IMAGES_DIR = _path("THREAD_RESULT_IMAGES_DIR", BASE_DIR / "result_images")
FRONTEND_OUT = RESOURCE_DIR / "frontend" / "out"
LOG_DIR = _path("THREAD_LOG_DIR", BASE_DIR / "logs")
OPEN_BROWSER = os.getenv("OPEN_BROWSER", "1" if FROZEN else "0") == "1"

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# Highest OpenCV device index probed when listing borescope/USB cameras.
CAMERA_PROBE_MAX = int(os.getenv("CAMERA_PROBE_MAX", "6"))
# Requested capture size (borescopes are typically 1280x720).
CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "1280"))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "720"))
# Optional device indices to open at startup, e.g. CODE_CAMERA_INDEX=0 THREAD_CAMERA_INDEX=1
CODE_CAMERA_INDEX = os.getenv("CODE_CAMERA_INDEX")
THREAD_CAMERA_INDEX = os.getenv("THREAD_CAMERA_INDEX")

# SHA-256 of the SETUP password. Same default as cut-mark-station (same client).
DEFAULT_PASSWORD_HASH = "f01fd40abb0c501e1207cb810d1e1cd9f22df8b1cccf7b071b0d2ae060e12fc6"
