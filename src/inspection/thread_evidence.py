"""Physics-based thread-ring evidence for borescope images.

A tapped hole seen through a borescope shows many fine, closely spaced ridges that run
*tangentially* around the bore axis (the thread crests).  A plain drilled hole shows a smooth
wall; a hand / glove / random object shows no bore-centred structure; a view from far away
shows the bore so small that the thread pitch is no longer resolved.

``measure_thread_evidence`` returns interpretable quantities that the classifier and the
GO / NO-GO gates use:

* ``ridge_area``   – native-resolution pixel area covered by fine ridges that are tangential
                     to the estimated bore centre (thread crests).
* ``purity``       – fraction of all fine texture that is such a tangential ridge (thread
                     texture is highly ordered; scratches, skin, scenery are not).
* ``band_px``      – radial extent (native px) of the ridge zone (small when seen from far).
* ``symmetry``     – strength of the radial-symmetry vote peak (a bore is present at all).
* ``coverage``     – fraction of 10° sectors around the centre that contain ridges.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np

WORK_WIDTH = 960          # analysis width; thread pitch stays resolvable at this scale
NATIVE_WIDTH = 1280       # borescope native width; areas are reported in these pixels
_MAX_REFINE_POINTS = 8000


@dataclass(frozen=True)
class ThreadEvidence:
    ridge_area: float
    purity: float
    band_px: float
    symmetry: float
    coverage: float
    center_x: float       # in input-image pixels
    center_y: float

    def as_dict(self) -> dict:
        return {k: round(float(v), 4) for k, v in asdict(self).items()}

    def vector(self) -> np.ndarray:
        return np.array([
            np.log1p(self.ridge_area), self.purity, np.log1p(self.band_px),
            np.log1p(self.symmetry), self.coverage,
        ], dtype=np.float32)


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image is None or not isinstance(image, np.ndarray) or image.ndim not in (2, 3) or image.size == 0:
        raise ValueError("image must be a non-empty 2D or 3D numpy array")
    if image.ndim == 3:
        if image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image.astype(np.float32)


def _vote_center(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Radial-symmetry vote: every strong edge votes along its gradient line."""
    height, width = gray.shape
    smooth = cv2.GaussianBlur(gray, (0, 0), 2.5)
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1)
    mag = np.hypot(gx, gy) + 1e-6
    ys, xs = np.nonzero(mag > np.percentile(mag, 85))
    if len(xs) > 30000:
        pick = np.random.default_rng(0).choice(len(xs), 30000, replace=False)
        ys, xs = ys[pick], xs[pick]
    ux, uy = gx[ys, xs] / mag[ys, xs], gy[ys, xs] / mag[ys, xs]
    step = 2
    acc = np.zeros((height // step + 1, width // step + 1), np.float32)
    for distance in range(10, int(0.7 * height), 4):
        for sign in (1, -1):
            cx = ((xs + sign * distance * ux) / step).astype(np.int32)
            cy = ((ys + sign * distance * uy) / step).astype(np.int32)
            ok = (cx >= 0) & (cx < acc.shape[1]) & (cy >= 0) & (cy < acc.shape[0])
            np.add.at(acc, (cy[ok], cx[ok]), 1.0)
    acc = cv2.GaussianBlur(acc, (0, 0), 3)
    _, peak, _, loc = cv2.minMaxLoc(acc)
    return np.array([loc[0] * step, loc[1] * step], float), float(peak / (acc.mean() + 1e-6))


def _refine_center(center: np.ndarray, xs, ys, ux, uy, weight, iterations: int = 8) -> np.ndarray:
    """Robust least-squares point closest to all ridge normal lines (handles off-axis views)."""
    points = np.stack([xs, ys], 1).astype(np.float64)
    normals = np.stack([ux, uy], 1).astype(np.float64)
    projector = np.eye(2)[None] - normals[:, :, None] * normals[:, None, :]
    for _ in range(iterations):
        delta = center - points
        perpendicular = delta - (delta * normals).sum(1, keepdims=True) * normals
        residual = np.linalg.norm(perpendicular, axis=1)
        w = weight * np.minimum(1.0, 12.0 / (residual + 1e-6))
        a = (w[:, None, None] * projector).sum(0)
        b = (w[:, None, None] * (projector @ points[:, :, None])).sum(0)[:, 0]
        center = np.linalg.solve(a + 1e-6 * np.eye(2), b)
    return center


def measure_thread_evidence(image: np.ndarray) -> ThreadEvidence:
    gray0 = _to_gray(image)
    scale = WORK_WIDTH / gray0.shape[1]
    gray = cv2.resize(gray0, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    height, width = gray.shape

    band = cv2.GaussianBlur(gray, (0, 0), 1.0) - cv2.GaussianBlur(gray, (0, 0), 3.0)
    contrast = cv2.GaussianBlur(np.abs(band), (0, 0), 8) / (cv2.GaussianBlur(gray, (0, 0), 8) + 8)
    gx = cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(band, cv2.CV_32F, 0, 1, ksize=3)
    jxx = cv2.GaussianBlur(gx * gx, (0, 0), 4)
    jyy = cv2.GaussianBlur(gy * gy, (0, 0), 4)
    jxy = cv2.GaussianBlur(gx * gy, (0, 0), 4)
    coherence = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-6)
    theta = 0.5 * np.arctan2(2 * jxy, jxx - jyy)

    center, symmetry = _vote_center(gray)
    candidates = (coherence > 0.45) & (contrast > 0.02)
    ys, xs = np.nonzero(candidates[::3, ::3])
    ys, xs = ys * 3, xs * 3
    if len(xs) > _MAX_REFINE_POINTS:
        pick = np.random.default_rng(1).choice(len(xs), _MAX_REFINE_POINTS, replace=False)
        ys, xs = ys[pick], xs[pick]
    if len(xs) > 50:
        refined = _refine_center(center, xs, ys, np.cos(theta[ys, xs]), np.sin(theta[ys, xs]), coherence[ys, xs])
        # Keep the centre within a generous margin of the frame; otherwise trust the vote.
        if -0.5 * width < refined[0] < 1.5 * width and -0.5 * height < refined[1] < 1.5 * height:
            center = refined
    cx, cy = center

    yy, xx = np.mgrid[0:height, 0:width]
    phi = np.arctan2(yy - cy, xx - cx)
    radius = np.hypot(xx - cx, yy - cy)
    texture = (contrast > 0.02) & (radius > 8)
    ridge = texture & (coherence > 0.45) & (np.abs(np.cos(theta - phi)) > 0.85)

    to_native = NATIVE_WIDTH / WORK_WIDTH
    ridge_count = int(ridge.sum())
    ridge_area = ridge_count * to_native ** 2
    purity = ridge_count / (int(texture.sum()) + 1)
    if ridge_count > 100:
        sectors = ((phi[ridge] + np.pi) / (2 * np.pi) * 36).astype(int) % 36
        coverage = len(np.unique(sectors)) / 36
        r5, r95 = np.percentile(radius[ridge], [5, 95])
        band_px = (r95 - r5) * to_native
    else:
        coverage, band_px = 0.0, 0.0
    return ThreadEvidence(
        ridge_area=float(ridge_area), purity=float(purity), band_px=float(band_px),
        symmetry=float(symmetry), coverage=float(coverage),
        center_x=float(cx / scale), center_y=float(cy / scale),
    )


def ridge_mask(image: np.ndarray, evidence: ThreadEvidence) -> np.ndarray:
    """Boolean mask (input resolution) of detected thread ridges, for operator overlays."""
    gray0 = _to_gray(image)
    scale = WORK_WIDTH / gray0.shape[1]
    gray = cv2.resize(gray0, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    height, width = gray.shape
    band = cv2.GaussianBlur(gray, (0, 0), 1.0) - cv2.GaussianBlur(gray, (0, 0), 3.0)
    contrast = cv2.GaussianBlur(np.abs(band), (0, 0), 8) / (cv2.GaussianBlur(gray, (0, 0), 8) + 8)
    gx = cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(band, cv2.CV_32F, 0, 1, ksize=3)
    jxx = cv2.GaussianBlur(gx * gx, (0, 0), 4)
    jyy = cv2.GaussianBlur(gy * gy, (0, 0), 4)
    jxy = cv2.GaussianBlur(gx * gy, (0, 0), 4)
    coherence = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-6)
    theta = 0.5 * np.arctan2(2 * jxy, jxx - jyy)
    cx, cy = evidence.center_x * scale, evidence.center_y * scale
    yy, xx = np.mgrid[0:height, 0:width]
    phi = np.arctan2(yy - cy, xx - cx)
    radius = np.hypot(xx - cx, yy - cy)
    ridge = (contrast > 0.02) & (radius > 8) & (coherence > 0.45) & (np.abs(np.cos(theta - phi)) > 0.85)
    return cv2.resize(ridge.astype(np.uint8), (gray0.shape[1], gray0.shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)
