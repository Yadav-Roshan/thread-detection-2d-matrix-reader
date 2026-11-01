from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.conftest import DATA

EXPECTED = "005715051079230AP666711026002315"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _files(code, thread):
    return {
        "code_image": ("code.jpg", BytesIO(code.read_bytes()), "image/jpeg"),
        "thread_image": ("thread.jpg", BytesIO(thread.read_bytes()), "image/jpeg"),
    }


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_ui_served(client):
    response = client.get("/")
    assert response.status_code == 200 and "<html" in response.text.lower()


def test_unknown_api_is_404(client):
    assert client.get("/api/nope").status_code == 404


def test_offline_go_without_rois(client):
    client.post("/api/config/rois", json={"code": None, "thread": None})
    response = client.post("/api/inspect/offline",
                           files=_files(DATA / "2D-Code/Snapshot000003.jpg", DATA / "Thread/Snapshot000000.jpg"),
                           data={"operator": "tester"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["overall_result"] == "GO"
    assert body["decoded_number"] == EXPECTED
    assert body["thread_result"]["state"] == "PRESENT"
    assert body["code_image"].startswith("data:image/jpeg;base64,")
    assert body["operator"] == "tester" and body["mode"] == "offline"
    assert isinstance(body["elapsed_ms"], int) and 0 < body["elapsed_ms"] < 30_000


def test_offline_no_thread_is_no_go_and_recorded(client):
    body = client.post("/api/inspect/offline",
                       files=_files(DATA / "2D-Code/Snapshot000000.jpg", DATA / "No-Thread/Snapshot000023.jpg")).json()
    assert body["overall_result"] == "NO_GO"
    assert body["thread_result"]["state"] in ("ABSENT", "NOT_VISIBLE")
    records = client.get(f"/api/reports/by-part/{EXPECTED}").json()
    assert any(rec["report_id"] == body["report_id"] and rec["overall_result"] == "NO_GO" for rec in records)
    assert "/NO_GO/" in body["report_id"]


def test_unreadable_code_is_no_go(client):
    body = client.post("/api/inspect/offline",
                       files=_files(DATA / "Thread/Snapshot000000.jpg", DATA / "Thread/Snapshot000000.jpg")).json()
    assert body["overall_result"] == "NO_GO"
    assert body["decoded_number"] is None and body["code_result"]["passed"] is False


def test_saved_rois_are_used(client):
    saved = client.post("/api/config/rois", json={
        "code": {"x": 430, "y": 290, "width": 200, "height": 300}, "thread": None}).json()
    assert saved["code"]["width"] == 200 and saved["thread"] is None
    body = client.post("/api/inspect/offline",
                       files=_files(DATA / "2D-Code/Snapshot000003.jpg", DATA / "Thread/Snapshot000000.jpg")).json()
    assert body["decoded_number"] == EXPECTED
    client.post("/api/config/rois", json={"code": None, "thread": None})


def test_zero_size_roi_means_full_frame(client):
    response = client.post("/api/config/rois", json={"code": {"x": 0, "y": 0, "width": 0, "height": 0}, "thread": None})
    assert response.json()["code"] is None


def test_bad_upload(client):
    response = client.post("/api/inspect/offline", files={
        "code_image": ("a.jpg", BytesIO(b"not an image"), "image/jpeg"),
        "thread_image": ("b.jpg", BytesIO(b"x"), "image/jpeg")})
    assert response.status_code == 400


def test_online_without_cameras_is_503(client):
    response = client.post("/api/inspect", json={"operator": ""})
    assert response.status_code == 503 and "camera" in response.json()["detail"]


def test_detection_params_roundtrip(client):
    defaults = client.get("/api/config/detection-defaults").json()
    assert client.post("/api/config/detection", json={**defaults, "min_ridge_area": 90000}).json()["min_ridge_area"] == 90000
    assert client.get("/api/config/detection").json()["min_ridge_area"] == 90000
    assert client.post("/api/config/detection", json={**defaults, "min_purity": 3}).status_code == 400
    assert client.post("/api/config/detection", json={"min_ring_score": 1}).status_code == 400
    client.post("/api/config/detection", json=defaults)


def test_auth_and_change_password(client):
    assert client.post("/api/auth/login", json={"password": "definitely-wrong"}).json()["success"] is False
    assert client.post("/api/auth/change-password",
                       json={"old_password": "wrong", "new_password": "abcdef"}).json()["success"] is False


def test_reports_recent(client):
    rows = client.get("/api/reports?limit=5").json()
    assert 0 < len(rows) <= 5
    assert {"report_id", "timestamp", "decoded_number", "overall_result", "report_url"} <= rows[0].keys()


def test_camera_endpoints_without_hardware(client):
    assert client.get("/api/camera/thread/status").json()["connected"] is False
    assert client.get("/api/camera/bogus/status").status_code == 404
    assert client.post("/api/camera/code/save-frame").status_code == 503
