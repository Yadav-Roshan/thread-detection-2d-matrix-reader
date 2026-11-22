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


# ---------------------------------------------------------------- v1.2 regression: false PRESENT
CODE_FACES = sorted((DATA / "2D-Code").glob("*.jpg"))
BUGS = DATA / "Bugs"


@pytest.mark.parametrize("path", CODE_FACES, ids=lambda p: p.name)
def test_part_face_seen_from_outside_is_never_present(classifier, path):
    """Bug 2026-11-22: thread camera pointed at a part face (outside, not into the bore) said PRESENT."""
    assert classifier.predict(_img(path), None, P).state != "PRESENT"


@pytest.mark.parametrize("path", CODE_FACES, ids=lambda p: p.name)
def test_part_face_rotated_or_zoomed_is_never_present(classifier, path):
    img = _img(path)
    h, w = img.shape[:2]
    zoomed = cv2.resize(img[h // 6: h - h // 6, w // 5: w - w // 5], (w, h))
    for view in (cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE), cv2.flip(zoomed, 0)):
        assert classifier.predict(cv2.resize(view, (w, h)), None, P).state != "PRESENT"


@pytest.mark.parametrize("name,box", [
    ("False-Thread-Detection-22-11-2026_1.jpg", (288, 486, 688, 1058)),
    ("False-Thread-Detection-22-11-2026_2.jpg", (248, 482, 767, 1125)),
], ids=["room-scene", "part-face"])
def test_reported_bug_scenes_are_not_present(classifier, name, box):
    photo = cv2.imread(str(BUGS / name))
    y0, y1, x0, x1 = box
    scene = cv2.resize(photo[y0:y1, x0:x1], (1280, 720))
    assert classifier.predict(scene, None, P).state != "PRESENT"


def test_held_out_reject_scenes_metric(classifier):
    assert classifier.metrics["reject_images"] >= 5
    assert classifier.metrics["reject_images_accepted"] == 0


def test_thread_result_image_has_no_mask_overlay(classifier):
    from src.inspection.pipeline import run_thread_step
    img = _img(THREAD[3])
    step = run_thread_step(img, None, classifier, P)
    assert step.result["state"] == "PRESENT"
    assert np.array_equal(step.image, img)          # no mask, no marker: frame as captured
    roi = (100, 50, 900, 600)
    boxed = run_thread_step(img, roi, classifier, P).image
    inside = (slice(60, 640), slice(110, 990))
    assert np.array_equal(boxed[inside], img[inside])  # only the ROI outline is drawn
