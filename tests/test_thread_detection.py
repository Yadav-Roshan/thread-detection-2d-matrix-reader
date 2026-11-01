"""Thread decision must be fail-safe: PRESENT only when thread rings are actually visible."""
import cv2
import numpy as np
import pytest

from src.inspection.thread_classifier import DetectionParams, crop_roi
from src.inspection.thread_evidence import measure_thread_evidence
from tests.conftest import DATA

THREAD = sorted((DATA / "Thread").glob("*.jpg"))
NO_THREAD = sorted((DATA / "No-Thread").glob("*.jpg"))
P = DetectionParams()


def _img(path):
    return cv2.imread(str(path))


def far_view(image, scale=0.25, seed=0):
    """Borescope pulled back: bore is small, surroundings are blurred metal."""
    rng = np.random.default_rng(seed)
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    bg = cv2.GaussianBlur(image, (0, 0), 25)
    bg = (bg * 0.6 + 90).clip(0, 255).astype(np.uint8)
    h, w = small.shape[:2]
    y = (720 - h) // 2 + int(rng.integers(-60, 60))
    x = (1280 - w) // 2 + int(rng.integers(-120, 120))
    bg[y:y + h, x:x + w] = small
    return cv2.GaussianBlur(bg, (0, 0), 1.2)


def hand_over(image, seed=1):
    """Skin-toned occluder covering most of the view."""
    rng = np.random.default_rng(seed)
    img = image.astype(np.float32)
    mask = np.zeros(img.shape[:2], np.float32)
    centre = (640 + int(rng.integers(-150, 150)), 360)
    cv2.ellipse(mask, centre, (600, 420), int(rng.integers(0, 180)), 0, 360, 1, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), 40)[..., None]
    skin = np.full_like(img, (120, 150, 205))
    noise = cv2.GaussianBlur(rng.standard_normal(img.shape[:2]).astype(np.float32), (0, 0), 6)
    skin += noise[..., None] * 25
    return (img * (1 - mask) + skin * mask).clip(0, 255).astype(np.uint8)


def test_evidence_is_deterministic():
    image = _img(THREAD[0])
    assert measure_thread_evidence(image) == measure_thread_evidence(image)


def test_evidence_separates_thread_from_plain_bore():
    thread = np.median([measure_thread_evidence(_img(p)).ridge_area for p in THREAD[::8]])
    plain = np.median([measure_thread_evidence(_img(p)).ridge_area for p in NO_THREAD[::4]])
    assert thread > 2 * plain


@pytest.mark.parametrize("path", THREAD[::10], ids=lambda p: p.name)
def test_thread_images_present(classifier, path):
    decision = classifier.predict(_img(path), None, P)
    assert decision.state == "PRESENT", decision.evidence


@pytest.mark.parametrize("path", NO_THREAD[::4], ids=lambda p: p.name)
def test_no_thread_images_never_present(classifier, path):
    assert classifier.predict(_img(path), None, P).state != "PRESENT"


@pytest.mark.parametrize("path", THREAD[::12], ids=lambda p: p.name)
def test_hand_in_front_is_not_present(classifier, path):
    assert classifier.predict(hand_over(_img(path)), None, P).state != "PRESENT"


@pytest.mark.parametrize("path", THREAD[::12], ids=lambda p: p.name)
def test_far_view_is_not_present(classifier, path):
    assert classifier.predict(far_view(_img(path)), None, P).state != "PRESENT"


def _blank():
    return np.full((720, 1280, 3), 128, np.uint8)


def _dark():
    return np.zeros((720, 1280, 3), np.uint8)


def _skin():
    return np.full((720, 1280, 3), (120, 150, 205), np.uint8)


def _noise():
    return (np.random.default_rng(3).random((720, 1280, 3)) * 255).astype(np.uint8)


@pytest.mark.parametrize("make", [_blank, _dark, _skin, _noise], ids=["blank", "dark", "skin", "noise"])
def test_non_bore_scenes_are_rejected(classifier, make):
    decision = classifier.predict(make(), None, P)
    assert decision.state != "PRESENT" and not decision.passed


def test_defocused_thread_is_not_present(classifier):
    blurred = cv2.GaussianBlur(_img(THREAD[5]), (0, 0), 9)
    assert classifier.predict(blurred, None, P).state != "PRESENT"


def test_roi_crop_and_validation():
    image = _img(THREAD[0])
    assert crop_roi(image, (10, 20, 100, 50)).shape[:2] == (50, 100)
    with pytest.raises(ValueError):
        crop_roi(image, (1200, 0, 200, 100))


def test_stricter_params_reject(classifier):
    strict = DetectionParams(min_ridge_area=10_000_000)
    assert classifier.predict(_img(THREAD[0]), None, strict).state != "PRESENT"


def test_cv_metrics_recorded(classifier):
    metrics = classifier.metrics
    assert metrics["false_accept_rate"] == 0.0
    assert metrics["thread_recall"] >= 0.9
