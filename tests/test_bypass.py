"""Bypass 2D reader: operator-entered job number, thread check only, persisted password-protected flag."""
import hashlib
import json
import socket
from pathlib import Path

import cv2
import pytest
from fastapi.testclient import TestClient

from main import app
from src.server.routes import State
from src.storage.settings_store import SettingsStore
from tests.conftest import DATA

THREAD = DATA / "Thread/Snapshot000000.jpg"
NO_THREAD = DATA / "No-Thread/Snapshot000023.jpg"
PASSWORD = "bypass-test-1"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        auth = State.settings.dir / "auth.json"
        auth.parent.mkdir(parents=True, exist_ok=True)
        auth.write_text(json.dumps({"password_hash": hashlib.sha256(PASSWORD.encode()).hexdigest()}), encoding="utf-8")
        yield c
        c.post("/api/station/bypass", json={"enabled": False, "password": PASSWORD})


@pytest.fixture
def cameras(monkeypatch):
    frames = {"code": None, "thread": cv2.imread(str(THREAD))}   # 2D code camera absent on purpose
    monkeypatch.setattr(State.cameras, "frame", lambda role: frames.get(role))
    monkeypatch.setattr(State.cameras, "averaged_frame", lambda role, count=3: frames.get(role))
    return frames


def test_toggle_requires_password_and_persists(client):
    assert client.get("/api/station/mode").json()["bypass_code_reader"] is False
    r = client.post("/api/station/bypass", json={"enabled": True, "password": "wrong"})
    assert r.status_code == 403 and client.get("/api/station/mode").json()["bypass_code_reader"] is False
    assert client.post("/api/station/bypass", json={"enabled": True, "password": PASSWORD}).json()["bypass_code_reader"]
    # survives an application restart (fresh settings object reading the same folder)
    assert SettingsStore(State.settings.dir).get_bypass_code_reader() is True


def test_job_number_validation(client):
    client.post("/api/station/bypass", json={"enabled": True, "password": PASSWORD})
    assert client.post("/api/station/job", json={"job_number": "bad job!"}).status_code == 400
    assert client.post("/api/station/job", json={"job_number": "  JOB-42/A.1  "}).json()["job_number"] == "JOB-42/A.1"
    assert client.post("/api/station/job", json={"job_number": ""}).json()["job_number"] is None


def test_manual_check_uses_job_number_and_clears_it(client, cameras, tmp_path):
    client.post("/api/station/bypass", json={"enabled": True, "password": PASSWORD})
    client.post("/api/report/set-directory", json={"directory": str(tmp_path)})
    assert client.post("/api/inspect", json={"operator": "op"}).status_code == 400      # no job number
    body = client.post("/api/inspect", json={"operator": "op", "job_number": "JOB-1001"}).json()
    assert body["overall_result"] == "GO" and body["decoded_number"] == "JOB-1001"
    assert body["code_result"]["format"] == "Job number (manual)" and body["code_image"] is None
    assert body["job_number_cleared"] is True and client.get("/api/station/mode").json()["job_number"] is None
    assert "/GO/JOB-1001_" in body["report_id"] and (tmp_path / body["report_id"]).is_file()
    cameras["thread"] = cv2.imread(str(NO_THREAD))
    nogo = client.post("/api/inspect", json={"job_number": "JOB-1002"}).json()
    assert nogo["overall_result"] == "NO_GO" and "/NO_GO/JOB-1002_" in nogo["report_id"]
    client.post("/api/report/set-directory", json={"directory": None})


def test_job_entered_beforehand_is_used(client, cameras):
    client.post("/api/station/bypass", json={"enabled": True, "password": PASSWORD})
    client.post("/api/station/job", json={"job_number": "JOB-77"})
    assert client.post("/api/inspect", json={}).json()["decoded_number"] == "JOB-77"


def test_job_entry_rejected_when_bypass_off(client):
    client.post("/api/station/bypass", json={"enabled": False, "password": PASSWORD})
    assert client.post("/api/station/job", json={"job_number": "JOB-1"}).status_code == 400


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_hercules_cycle_in_bypass_mode(client, cameras):
    client.post("/api/station/bypass", json={"enabled": True, "password": PASSWORD})
    port = _free_port()
    client.post("/api/config/hercules", json={"enabled": True, "bind_host": "127.0.0.1", "port": port})
    sock = socket.create_connection(("127.0.0.1", port), timeout=30)
    buf = b""

    def ask(cmd):
        nonlocal buf
        sock.sendall(cmd.encode() + b"\r\n")
        while b"\r\n" not in buf:
            buf += sock.recv(1024)
        line, buf = buf.split(b"\r\n", 1)
        return line.decode()

    try:
        assert ask("isready") == "readyyes"           # 2D code camera not needed in bypass mode
        assert ask("scanjobno") == "noread"           # nothing entered yet
        client.post("/api/station/job", json={"job_number": "JOB-555"})
        assert ask("scanjobno") == "JOB-555"          # job number typed on the UI goes to Hercules
        assert ask("threadpresence") == "threadok"
        event = client.get("/api/hercules/status").json()["last_event"]
        assert event["result"]["decoded_number"] == "JOB-555" and "/GO/JOB-555_" in event["result"]["report_id"]
        assert client.get("/api/station/mode").json()["job_number"] is None   # cleared after the thread check
        client.post("/api/station/job", json={"job_number": "JOB-556"})
        assert ask("threadpresence") == "threadok"    # thread check without scan uses the entered number
        assert client.get("/api/hercules/status").json()["last_event"]["result"]["decoded_number"] == "JOB-556"
    finally:
        sock.close()
        client.post("/api/config/hercules", json={"enabled": False})


def test_pdf_labels_manual_job_number(tmp_path):
    from datetime import datetime
    from src.inspection.pipeline import manual_code_step
    from src.outputs.pdf_report import ReportWriter
    code = manual_code_step("JOB-77")
    rid = ReportWriter(lambda: tmp_path).write(
        timestamp=datetime.now(), operator="op", mode="online", overall="GO", decoded_number="JOB-77",
        code_result=code.result, thread_result={"passed": True, "state": "PRESENT", "message": "ok"},
        code_image=None, thread_image=cv2.imread(str(THREAD)))
    raw = (tmp_path / rid).read_bytes()
    assert raw[:5] == b"%PDF-" and "/GO/JOB-77_" in "/" + rid
