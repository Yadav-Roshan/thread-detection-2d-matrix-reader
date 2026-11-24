"""Thread re-check: after a NOK the part identity is kept and only the thread is checked again."""
import hashlib
import json
import socket

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from main import app
from src.server.routes import State
from tests.conftest import DATA

CODE = DATA / "2D-Code/Snapshot000003.jpg"
PART = "005715051079230AP666711026002315"
THREAD = DATA / "Thread/Snapshot000000.jpg"
NO_THREAD = DATA / "No-Thread/Snapshot000023.jpg"
PASSWORD = "recheck-test-1"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        auth = State.settings.dir / "auth.json"
        auth.parent.mkdir(parents=True, exist_ok=True)
        auth.write_text(json.dumps({"password_hash": hashlib.sha256(PASSWORD.encode()).hexdigest()}), encoding="utf-8")
        c.post("/api/station/bypass", json={"enabled": False, "password": PASSWORD})
        yield c
        c.post("/api/station/bypass", json={"enabled": False, "password": PASSWORD})


@pytest.fixture
def cams(monkeypatch, client, tmp_path):
    frames = {"code": cv2.imread(str(CODE)), "thread": cv2.imread(str(NO_THREAD))}
    monkeypatch.setattr(State.cameras, "frame", lambda role: frames.get(role))
    monkeypatch.setattr(State.cameras, "averaged_frame", lambda role, count=3: frames.get(role))
    client.post("/api/station/recheck/clear")
    client.post("/api/report/set-directory", json={"directory": str(tmp_path)})
    yield frames
    client.post("/api/report/set-directory", json={"directory": None})
    client.post("/api/station/recheck/clear")


def mode(client):
    return client.get("/api/station/mode").json()


def test_nok_keeps_part_and_recheck_reuses_it(client, cams, tmp_path):
    first = client.post("/api/inspect", json={"operator": "op"}).json()
    assert first["overall_result"] == "NO_GO" and first["decoded_number"] == PART
    assert first["attempt"] == 1 and first["recheck"] is False and first["recheck_pending"] is True
    pending = mode(client)["recheck"]
    assert pending["part"] == PART and pending["attempts"] == 1 and pending["source"] == "2d"
    assert pending["last_state"] in ("ABSENT", "NOT_VISIBLE")

    cams["code"] = None                                   # code camera is NOT used by a re-check
    cams["thread"] = cv2.imread(str(THREAD))
    second = client.post("/api/inspect/recheck", json={"operator": "op"}).json()
    assert second["overall_result"] == "GO" and second["decoded_number"] == PART
    assert second["attempt"] == 2 and second["recheck"] is True and second["recheck_pending"] is False
    assert second["code_image"] is None and "kept from the earlier read" in second["code_result"]["message"]
    assert mode(client)["recheck"] is None                # cleared on GO
    assert (tmp_path / second["report_id"]).is_file() and f"/GO/{PART}_" in "/" + second["report_id"]


def test_repeated_nok_counts_attempts(client, cams):
    client.post("/api/inspect", json={})
    r2 = client.post("/api/inspect/recheck", json={}).json()
    r3 = client.post("/api/inspect/recheck", json={}).json()
    assert (r2["attempt"], r3["attempt"]) == (2, 3) and r3["recheck_pending"] is True
    assert mode(client)["recheck"]["attempts"] == 3


def test_recheck_without_pending_part_is_refused(client, cams):
    r = client.post("/api/inspect/recheck", json={})
    assert r.status_code == 400 and "No part waiting" in r.json()["detail"]


def test_new_part_clears_pending(client, cams):
    client.post("/api/inspect", json={})
    assert mode(client)["recheck"] is not None
    assert client.post("/api/station/recheck/clear").json()["recheck"] is None
    client.post("/api/inspect", json={})                 # NOK again -> pending
    cams["thread"] = cv2.imread(str(THREAD))
    client.post("/api/inspect", json={})                 # a normal CHECK = new part (GO) -> cleared
    assert mode(client)["recheck"] is None


def test_unread_code_is_never_kept_for_recheck(client, cams):
    cams["code"] = cv2.imread(str(THREAD))               # no Data Matrix in view
    r = client.post("/api/inspect", json={}).json()
    assert r["decoded_number"] is None and r["recheck_pending"] is False
    assert mode(client)["recheck"] is None


def test_recheck_needs_thread_camera(client, cams):
    client.post("/api/inspect", json={})
    cams["thread"] = None
    assert client.post("/api/inspect/recheck", json={}).status_code == 503
    assert mode(client)["recheck"] is not None           # still waiting


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Herc:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.buf = b""

    def ask(self, cmd):
        self.s.sendall(cmd.encode() + b"\r\n")
        while b"\r\n" not in self.buf:
            self.buf += self.s.recv(1024)
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line.decode()


@pytest.fixture
def herc(client):
    port = _free_port()
    client.post("/api/config/hercules", json={"enabled": True, "bind_host": "127.0.0.1", "port": port})
    h = _Herc(port)
    yield h
    h.s.close()
    client.post("/api/config/hercules", json={"enabled": False})


def last_result(client):
    return client.get("/api/hercules/status").json()["last_event"]["result"]


def test_hercules_threadpresence_again_rechecks_without_scan(client, cams, herc):
    assert herc.ask("scanjobno") == PART
    assert herc.ask("threadpresence") == "threadnok"
    assert mode(client)["recheck"]["part"] == PART
    cams["code"] = None                                   # prove the 2D code is not read again
    cams["thread"] = cv2.imread(str(THREAD))
    assert herc.ask("threadpresence") == "threadok"
    r = last_result(client)
    assert r["decoded_number"] == PART and r["attempt"] == 2 and r["recheck"] is True
    assert mode(client)["recheck"] is None
    assert herc.ask("threadpresence") == "threadok"       # nothing pending any more: part unknown
    assert last_result(client)["decoded_number"] is None


def test_hercules_scan_starts_new_part(client, cams, herc):
    assert herc.ask("scanjobno") == PART
    assert herc.ask("threadpresence") == "threadnok"
    assert herc.ask("scanjobno") == PART                  # new part cycle
    assert mode(client)["recheck"] is None


def test_hercules_thread_camera_error_keeps_part(client, cams, herc):
    assert herc.ask("scanjobno") == PART
    cams["thread"] = None
    assert herc.ask("threadpresence") == "error"
    assert mode(client)["recheck"]["last_state"] == "ERROR"
    cams["thread"] = cv2.imread(str(THREAD))
    assert herc.ask("threadpresence") == "threadok"
    assert last_result(client)["decoded_number"] == PART


def test_bypass_job_number_recheck(client, cams, herc):
    client.post("/api/station/bypass", json={"enabled": True, "password": PASSWORD})
    try:
        r = client.post("/api/inspect", json={"job_number": "JOB-R1"}).json()
        assert r["overall_result"] == "NO_GO" and r["recheck_pending"] is True
        assert mode(client)["recheck"] == {**mode(client)["recheck"], "part": "JOB-R1", "source": "job"}
        cams["thread"] = cv2.imread(str(THREAD))
        assert herc.ask("threadpresence") == "threadok"   # Hercules re-check uses the kept job number
        assert last_result(client)["decoded_number"] == "JOB-R1" and last_result(client)["attempt"] == 2
        cams["thread"] = cv2.imread(str(NO_THREAD))
        client.post("/api/inspect", json={"job_number": "JOB-R2"})
        assert mode(client)["recheck"]["part"] == "JOB-R2"
        client.post("/api/station/job", json={"job_number": "JOB-R3"})   # typing a new job = new part
        assert mode(client)["recheck"] is None
    finally:
        client.post("/api/station/bypass", json={"enabled": False, "password": PASSWORD})


def test_code_banner_is_large_and_full_width():
    from src.inspection.pipeline import run_code_step
    img = cv2.imread(str(CODE))
    out = run_code_step(img, None).image
    h, w = out.shape[:2]
    band = out[int(h * 0.88):, :]
    green = (band[..., 1] > 120) & (band[..., 0] < 110) & (band[..., 2] < 110)
    white = (band > 200).all(axis=2)
    assert green.mean() > 0.4                              # full-width green banner
    rows = np.where(white.any(axis=1))[0]
    cols = np.where(white.any(axis=0))[0]
    assert rows.max() - rows.min() > h * 0.045             # text >= 4.5% of image height (old overlay: ~2.5%)
    assert cols.max() - cols.min() > w * 0.8               # spans most of the width
