from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from src.inspection import matrix_reader
from src.inspection.matrix_reader import DecodeError, decode_array, decode_image, decode_directory

DATASET = Path(__file__).resolve().parents[1] / "data" / "2D-Code"
EXPECTED = "005715051079230AP666711026002315"


def test_decodes_a_supplied_roi():
    result = decode_image(DATASET / "Snapshot000000.jpg", roi=(500, 80, 300, 300))
    assert result.text == EXPECTED
    assert result.format == "DataMatrix"


def test_decodes_all_dataset_rois():
    rois = {
        "Snapshot000000.jpg": (500, 80, 300, 300),
        "Snapshot000003.jpg": (430, 290, 200, 300),
        "Snapshot000004.jpg": (370, 200, 200, 300),
        "Snapshot000005.jpg": (630, 0, 240, 300),
        "Snapshot000007.jpg": (0, 0, 350, 350),
    }
    results = decode_directory(DATASET, rois)
    assert set(results) == set(rois)
    assert all(r.text == EXPECTED for r in results.values())


def test_rejects_non_data_matrix_barcode(monkeypatch):
    image = cv2.imread(str(DATASET / "Snapshot000000.jpg"))

    monkeypatch.setattr(
        matrix_reader.zxingcpp,
        "read_barcodes",
        lambda *args, **kwargs: [SimpleNamespace(text="not-a-matrix", format="QRCode")],
    )

    with pytest.raises(DecodeError):
        decode_array(image, (500, 80, 300, 300), max_seconds=0.2)


@pytest.mark.parametrize("name", ["Snapshot000000.jpg", "Snapshot000003.jpg", "Snapshot000004.jpg",
                                  "Snapshot000005.jpg", "Snapshot000007.jpg"])
def test_decodes_without_roi(name):
    """ROIs are optional: the reader searches the full frame."""
    image = cv2.imread(str(DATASET / name))
    assert decode_array(image, None, max_seconds=5).text == EXPECTED


def test_thread_image_has_no_code():
    image = cv2.imread(str(DATASET.parent / "Thread" / "Snapshot000000.jpg"))
    with pytest.raises(DecodeError):
        decode_array(image, None, max_seconds=1.0)
