"""PDF reports (cut-mark-station folder structure), report directory, and the Hercules TCP link."""
import asyncio
import re
import socket
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import cv2
import pytest
from fastapi.testclient import TestClient

from main import app
from src.outputs.hercules_server import HerculesServer, normalize_config
from src.outputs.pdf_report import ReportWriter
from src.server.routes import State
from tests.conftest import DATA

EXPECTED = "005715051079230AP666711026002315"
CODE = DATA / "2D-Code/Snapshot000003.jpg"
THREAD = DATA / "Thread/Snapshot000000.jpg"
NO_THREAD = DATA / "No-Thread/Snapshot000023.jpg"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _files(code, thread):
    return {"code_image": ("c.jpg", BytesIO(code.read_bytes()), "image/jpeg"),
            "thread_image": ("t.jpg", BytesIO(thread.read_bytes()), "image/jpeg")}


# ------------------------------------------------------------------ PDF + structure
def test_pdf_saved_in_date_and_verdict_folders(client, tmp_path):
    assert client.post("/api/report/set-directory", json={"directory": str(tmp_path)}).json()["success"]
    body = client.post("/api/inspect/offline", files=_files(CODE, THREAD), data={"operator": "op1"}).json()
    assert body["overall_result"] == "GO"
    today = datetime.now()
    assert re.fullmatch(rf"{today:%Y}/{today:%m}/{today:%d}/GO/{EXPECTED}_\d{{6}}\.pdf", body["report_id"])
    pdf = tmp_path / body["report_id"]
    assert pdf.is_file() and pdf.read_bytes()[:5] == b"%PDF-" and pdf.stat().st_size > 20_000

    nogo = client.post("/api/inspect/offline", files=_files(CODE, NO_THREAD)).json()
    assert "/NO_GO/" in nogo["report_id"] and (tmp_path / nogo["report_id"]).is_file()

    served = client.get(body["report_url"])
    assert served.status_code == 200 and served.headers["content-type"] == "application/pdf"
    assert served.content[:5] == b"%PDF-"

    recent = client.get("/api/reports?limit=10").json()
    assert {r["report_id"] for r in recent} >= {body["report_id"], nogo["report_id"]}
    assert recent[0]["timestamp"] >= recent[-1]["timestamp"]
    by_part = client.get(f"/api/reports/by-part/{EXPECTED}").json()
    assert {r["overall_result"] for r in by_part} == {"GO", "NO_GO"}
    client.post("/api/report/set-directory", json={"directory": None})


def test_unread_code_report_named_unread(client, tmp_path):
    client.post("/api/report/set-directory", json={"directory": str(tmp_path)})
    body = client.post("/api/inspect/offline", files=_files(THREAD, THREAD)).json()
    assert "/NO_GO/UNREAD_" in body["report_id"]
    assert client.get("/api/reports/by-part/UNREAD").json()[0]["decoded_number"] is None
    client.post("/api/report/set-directory", json={"directory": None})


def test_same_second_reports_do_not_overwrite(tmp_path):
    writer = ReportWriter(lambda: tmp_path)
    now = datetime.now()
    kwargs = dict(timestamp=now, operator="", mode="offline", overall="GO", decoded_number="ABC",
                  code_result={"passed": True, "text": "ABC"}, thread_result={"passed": True, "state": "PRESENT"},
                  code_image=None, thread_image=None)
    a, b = writer.write(**kwargs), writer.write(**kwargs)
    assert a != b and len(writer.by_part("ABC")) == 2


def test_report_path_traversal_blocked(client):
    assert client.get("/api/reports/file/../../config/auth.json").status_code == 404
    assert client.get("/api/reports/file/2026/01/01/GO/missing.pdf").status_code == 404


def test_report_directory_validation(client):
    current = client.get("/api/report/directory").json()
    assert current["directory"] and "YYYY" in current["structure"]
    assert client.post("/api/report/set-directory", json={"directory": "relative/path"}).status_code == 400


# ------------------------------------------------------------------ Hercules
def test_hercules_config_validation(client):
    defaults = client.get("/api/config/hercules").json()
    assert defaults["ready_expect"] == "isready" and defaults["scan_expect"] == "scanjobno"
    assert defaults["thread_expect"] == "threadpresence" and defaults["thread_ok_reply"] == "threadok"
    assert client.post("/api/config/hercules", json={"port": 0}).status_code == 400
    assert client.post("/api/config/hercules", json={"scan_expect": "isready"}).status_code == 400
    assert client.post("/api/config/hercules", json={"bogus": 1}).status_code == 400
    with pytest.raises(ValueError):
        normalize_config({"ready_reply": "  "})


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Conn:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=20)
        self.buf = b""

    def ask(self, text: str) -> str:
        self.sock.sendall(text.encode() + b"\r\n")
        while b"\r\n" not in self.buf:
            chunk = self.sock.recv(1024)
            assert chunk, "server closed connection"
            self.buf += chunk
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line.decode()


def test_hercules_full_protocol_over_tcp(client, tmp_path, monkeypatch):
    frames = {"code": cv2.imread(str(CODE)), "thread": cv2.imread(str(THREAD))}
    monkeypatch.setattr(State.cameras, "frame", lambda role: frames.get(role))
    monkeypatch.setattr(State.cameras, "averaged_frame", lambda role, count=3: frames.get(role))
    client.post("/api/report/set-directory", json={"directory": str(tmp_path)})
    port = _free_port()
    cfg = client.post("/api/config/hercules", json={"enabled": True, "bind_host": "127.0.0.1", "port": port}).json()
    assert cfg["enabled"] and client.get("/api/hercules/status").json()["listening"]

    conn = _Conn(port)
    try:
        assert conn.ask("isready") == "readyyes"
        assert conn.ask("scanjobno") == EXPECTED
        status = client.get("/api/hercules/status").json()
        assert status["client_connected"] and status["last_event"]["kind"] == "scan"
        assert status["last_event"]["result"]["code_image"].startswith("data:image/jpeg")

        assert conn.ask("threadpresence") == "threadok"
        event = client.get("/api/hercules/status").json()["last_event"]
        assert event["kind"] == "thread" and event["reply"] == "threadok"
        assert event["result"]["overall_result"] == "GO" and event["result"]["decoded_number"] == EXPECTED
        assert (tmp_path / event["result"]["report_id"]).is_file()

        frames["thread"] = cv2.imread(str(NO_THREAD))
        assert conn.ask("SCANJOBNO") == EXPECTED            # commands are case-insensitive
        assert conn.ask("threadpresence") == "threadnok"
        assert conn.ask("hello") == "error"

        # thread check without a preceding scan -> report marked UNREAD, NO-GO
        frames["thread"] = cv2.imread(str(THREAD))
        assert conn.ask("threadpresence") == "threadok"
        assert "/NO_GO/UNREAD_" in client.get("/api/hercules/status").json()["last_event"]["result"]["report_id"]

        frames["code"] = cv2.imread(str(THREAD))            # no code in view
        assert conn.ask("scanjobno") == "noread"

        frames["code"] = None                               # camera unplugged
        assert conn.ask("isready") == "readyno"
        assert conn.ask("scanjobno") == "error"
    finally:
        conn.sock.close()
        client.post("/api/config/hercules", json={"enabled": False})
        client.post("/api/report/set-directory", json={"directory": None})
    time.sleep(0.2)
    assert client.get("/api/hercules/status").json()["listening"] is False


def test_hercules_custom_strings_and_no_newline(client, monkeypatch):
    frames = {"code": cv2.imread(str(CODE)), "thread": cv2.imread(str(THREAD))}
    monkeypatch.setattr(State.cameras, "frame", lambda role: frames.get(role))
    monkeypatch.setattr(State.cameras, "averaged_frame", lambda role, count=3: frames.get(role))
    port = _free_port()
    client.post("/api/config/hercules", json={"enabled": True, "bind_host": "127.0.0.1", "port": port,
                                              "ready_expect": "READY", "ready_reply": "YES"})
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        sock.sendall(b"READY")                              # Hercules "Send" without CR/LF
        assert sock.recv(64).strip() == b"YES"
        sock.close()
    finally:
        client.post("/api/config/hercules", json={"enabled": False, "ready_expect": "isready",
                                                  "ready_reply": "readyyes"})


def test_hercules_port_in_use_reports_error():
    async def scenario():
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        server = HerculesServer()
        await server.apply_config({"enabled": True, "bind_host": "127.0.0.1", "port": port})
        status = server.status()
        blocker.close()
        await server.stop()
        return status
    status = asyncio.run(scenario())
    assert status["listening"] is False and "Listen failed" in status["last_error"]
