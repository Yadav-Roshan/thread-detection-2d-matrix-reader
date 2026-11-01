"""ROI-based Data Matrix reader for low-resolution borescope images."""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import cv2
import numpy as np
import zxingcpp


@dataclass(frozen=True)
class DecodeResult:
    text: str
    format: str
    source: str
    roi: tuple[int, int, int, int]
    scale: float
    preprocessing: str


class DecodeError(RuntimeError):
    pass


def _validate_roi(roi: tuple[int, int, int, int], shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    if len(roi) != 4 or any(int(v) != v for v in roi):
        raise ValueError("ROI must be four integer values: x y width height")
    x, y, w, h = map(int, roi)
    height, width = shape[:2]
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
        raise ValueError(f"ROI {(x, y, w, h)} is outside image bounds {(width, height)}")
    return x, y, w, h


def _variants(crop: np.ndarray) -> Iterable[tuple[str, np.ndarray, float]]:
    """Yield deliberately redundant variants; robustness matters more than one-shot speed."""
    minimum = min(crop.shape[:2])
    blocks = [b for b in (31, 41, 51, 61) if b < minimum and b % 2]
    if not blocks:
        blocks = [max(3, minimum - (1 - minimum % 2))]
    for scale in (1.5, 2.0, 3.0, 4.0):
        up = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY) if up.ndim == 3 else up
        bases = (
            ("gray", gray),
            ("equalized", cv2.equalizeHist(gray)),
            ("clahe", cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)),
        )
        yield "color", up, scale
        for base_name, base in bases:
            yield base_name, base, scale
            yield base_name + "+otsu", cv2.threshold(base, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1], scale
            for block in blocks:
                # block size is expressed in original-pixel terms, then scaled for the upsampled image.
                scaled_block = max(3, int(round(block * scale)) | 1)
                for inverse, suffix in ((False, "adaptive"), (True, "adaptive-inv")):
                    threshold = cv2.adaptiveThreshold(
                        base, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY_INV if inverse else cv2.THRESH_BINARY,
                        scaled_block, 2,
                    )
                    yield f"{base_name}+{suffix}{block}", threshold, scale


def _fast_variants(crop: np.ndarray) -> Iterable[tuple[str, np.ndarray, float]]:
    """Cheap variants that cover most marks: plain, Otsu, and blurred adaptive threshold.

    Blurring before the adaptive threshold merges laser-hatched / dot-peened modules into
    solid cells, which is what makes etched part marks decodable.
    """
    gray0 = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    for scale in (1.0, 1.5, 2.0):
        gray = gray0 if scale == 1.0 else cv2.resize(gray0, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        if scale > 1.0:
            yield "color", cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC), scale
            yield "gray", gray, scale
            yield "gray+otsu", cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1], scale
        block = max(3, int(41 * scale) | 1)
        if block >= min(gray.shape[:2]):
            continue
        for sigma in (1.0, 2.0):
            blurred = cv2.GaussianBlur(gray, (0, 0), sigma * scale)
            yield (f"blur{sigma:g}+adaptive41",
                   cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 2),
                   scale)


def _decode_crop(crop: np.ndarray, deadline: float, quick: bool = False):
    """Try preprocessing variants on one crop. ``quick`` uses only the cheap variant set."""
    for preprocessing, variant, scale in (_fast_variants(crop) if quick else _variants(crop)):
        if time.monotonic() >= deadline:
            return None
        # ZXing handles rotation, but explicit rotations help on severely tilted marks.
        for quarter_turns in range(4):
            if time.monotonic() >= deadline:
                return None
            candidate = np.rot90(variant, quarter_turns).copy()
            for barcode in zxingcpp.read_barcodes(candidate, try_rotate=True, try_downscale=True):
                if str(barcode.format).replace(" ", "").lower() != "datamatrix" or not barcode.text:
                    continue
                return barcode.text, str(barcode.format).replace(" ", ""), scale, preprocessing
    return None


def _candidate_windows(image: np.ndarray, limit: int = 8) -> list[tuple[int, int, int, int]]:
    """Square windows ranked by Data-Matrix-like texture (dense, high-contrast module edges)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    energy = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).astype(np.float32)
    integral = cv2.integral(energy)
    windows: list[tuple[float, tuple[int, int, int, int]]] = []
    for size in sorted({int(min(height, width) * f) for f in (0.35, 0.5, 0.7)}):
        if size < 48:
            continue
        stride = max(16, size // 4)
        for y in range(0, height - size + 1, stride):
            for x in range(0, width - size + 1, stride):
                total = integral[y + size, x + size] - integral[y, x + size] - integral[y + size, x] + integral[y, x]
                windows.append((float(total) / size ** 1.5, (x, y, size, size)))
    windows.sort(key=lambda item: -item[0])
    chosen: list[tuple[int, int, int, int]] = []
    for _, win in windows:
        x, y, s, _ = win
        if all(abs(x - cx) > s // 3 or abs(y - cy) > s // 3 for cx, cy, _, _ in chosen):
            chosen.append(win)
        if len(chosen) >= limit:
            break
    return chosen


def decode_array(
    image: np.ndarray,
    roi: tuple[int, int, int, int] | None = None,
    source: str = "upload",
    max_seconds: float = 3.0,
) -> DecodeResult:
    """Decode a Data Matrix. ``roi=None`` (optional ROI) searches the whole frame."""
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    deadline = time.monotonic() + max_seconds
    if image is None or image.ndim not in (2, 3):
        raise ValueError("image must be a decoded 2D or 3D array")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    full = (0, 0, image.shape[1], image.shape[0])
    x, y, w, h = _validate_roi(roi, image.shape) if roi is not None else full
    crop = image[y:y + h, x:x + w]
    attempts: list[tuple[tuple[int, int, int, int], bool]] = [((x, y, w, h), True)]
    if (x, y, w, h) == full:
        attempts += [(win, True) for win in _candidate_windows(image)]
    attempts.append(((x, y, w, h), False))
    for (ax, ay, aw, ah), quick in attempts:
        found = _decode_crop(image[ay:ay + ah, ax:ax + aw], deadline, quick=quick)
        if found:
            text, fmt, scale, preprocessing = found
            return DecodeResult(text=text, format=fmt, source=source, roi=(ax, ay, aw, ah),
                                scale=scale, preprocessing=preprocessing)
        if time.monotonic() >= deadline:
            break
    del crop
    where = f"ROI {roi}" if roi is not None else "the full frame"
    raise DecodeError(f"No Data Matrix decoded from {where} within {max_seconds:g}s")


def decode_image(path: str | Path, roi: tuple[int, int, int, int]) -> DecodeResult:
    path = Path(path)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return decode_array(image, roi, str(path))


def decode_directory(directory: str | Path, rois: Mapping[str, tuple[int, int, int, int]]) -> dict[str, DecodeResult]:
    directory = Path(directory)
    results: dict[str, DecodeResult] = {}
    for name, roi in rois.items():
        results[name] = decode_image(directory / name, roi)
    return results


def _parse_roi(values: list[str]) -> tuple[int, int, int, int]:
    if len(values) != 4:
        raise argparse.ArgumentTypeError("ROI requires x y width height")
    try:
        return tuple(map(int, values))  # type: ignore[return-value]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ROI values must be integers") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode a Data Matrix from a supplied image ROI")
    parser.add_argument("image", type=Path, nargs="?", help="image to decode")
    parser.add_argument("--roi", nargs=4, metavar=("X", "Y", "W", "H"), required=False)
    parser.add_argument("--batch", type=Path, help="JSON file: {image_name: [x,y,w,h]}")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    if args.batch:
        config = json.loads(args.batch.read_text(encoding="utf-8"))
        directory = Path(config.get("directory", args.batch.parent))
        raw_rois = config.get("rois", config)
        rois = {name: tuple(values) for name, values in raw_rois.items()}
        output = {name: decode_image(directory / name, roi).__dict__ for name, roi in rois.items()}
    elif args.image and args.roi:
        output = decode_image(args.image, tuple(map(int, args.roi))).__dict__
    else:
        parser.error("provide IMAGE --roi X Y W H, or --batch FILE")
    print(json.dumps(output, indent=None if args.json else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
