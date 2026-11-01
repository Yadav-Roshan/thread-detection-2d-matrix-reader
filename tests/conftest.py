"""Isolate config, reports and frames into a temp dir before the app is imported."""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="thread-inspection-tests-"))
os.environ.setdefault("THREAD_CONFIG_DIR", str(_TMP / "config"))
os.environ.setdefault("THREAD_PDF_DIR", str(_TMP / "pdf"))
os.environ.setdefault("THREAD_FRAMES_DIR", str(_TMP / "frames"))
os.environ.setdefault("THREAD_RESULT_IMAGES_DIR", str(_TMP / "result_images"))

import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


@pytest.fixture(scope="session")
def classifier():
    from src.core import config
    from src.inspection.thread_classifier import ThreadClassifier
    try:
        return ThreadClassifier.load(config.MODEL_PATH)
    except (FileNotFoundError, ValueError):
        clf = ThreadClassifier.train(config.DATA_DIR)
        clf.save(config.MODEL_PATH)
        return clf
