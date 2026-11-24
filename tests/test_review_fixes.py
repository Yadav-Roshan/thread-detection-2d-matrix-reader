"""Regression tests for the v1.4.1 review of bypass / thread re-check / code banner.

Each test reproduces a scenario found by an independent review (traceability, Hercules replies, races,
file names, error paths, banner sizing) and asserts the fixed behaviour."""
import hashlib
import json
import socket
import threading
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import src.server.routes as R
from main import app
from src.core import config
from src.inspection.pipeline import CodeOutcome, annotate_code
from src.outputs.pdf_report import ReportWriter, _part_from_name, _safe_part
from src.server.routes import State
from tests.conftest import DATA

CODE = DATA / "2D-Code/Snapshot000003.jpg"
PART = "005715051079230AP666711026002315"
THREAD = DATA / "Thread/Snapshot000000.jpg"
NO_THREAD = DATA / "No-Thread/Snapshot000023.jpg"
PASSWORD = "review-fix-1"


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
    client.post("/api/station/bypass", json={"enabled": False, "password": PASSWORD})
    client.post("/api/station/recheck/clear")
    State.job_number = None


@pytest.fixture
def bypass(client):
    client.post("/api/station/bypass", json={"enabled": True, "password": PASSWORD})
    yield
    client.post("/api/station/bypass", json={"enabled": False, "password": PASSWORD})


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


def mode(client):
    return client.get("/api/station/mode").json()


def last(client):
    return client.get("/api/hercules/status").json()["last_event"]["result"]


# ------------------------------------------------------------------ Hercules reply follows the verdict
def test_threadpresence_after_new_part_replies_nok_for_unknown_part(client, cams, herc):
    assert herc.ask("scanjobno") == PART
    assert herc.ask("threadpresence") == "threadnok"
    client.post("/api/station/recheck/clear")              # operator: New part
    cams["thread"] = cv2.imread(str(THREAD))              # thread present, but the part is unknown now
    assert herc.ask("threadpresence") == "threadnok"
    r = last(client)
    assert r["overall_result"] == "NO_GO" and r["decoded_number"] is None and "/UNREAD_" in "/" + r["report_id"]


def test_bypass_noread_scan_is_not_replaced_by_a_number_typed_later(client, cams, herc, bypass):
    cams["thread"] = cv2.imread(str(THREAD))
    assert herc.ask("scanjobno") == "noread"              # Hercules was told: no part number
    client.post("/api/station/job", json={"job_number": "JOB-LATE"})
    assert herc.ask("threadpresence") == "threadnok"      # this cycle is unidentified -> NO-GO
    assert last(client)["decoded_number"] is None
    assert mode(client)["job_number"] == "JOB-LATE"       # kept for the next scanjobno
    assert herc.ask("scanjobno") == "JOB-LATE"
    assert herc.ask("threadpresence") == "threadok"
    assert last(client)["decoded_number"] == "JOB-LATE" and mode(client)["job_number"] is None


# ------------------------------------------------------------------ stale Hercules scan
def test_ui_check_drops_unpaired_hercules_scan_bypass(client, cams, herc, bypass):
    cams["thread"] = cv2.imread(str(THREAD))
    client.post("/api/station/job", json={"job_number": "JOB-J"})
    assert herc.ask("scanjobno") == "JOB-J"
    assert client.post("/api/inspect", json={}).json()["decoded_number"] == "JOB-J"   # operator pressed CHECK
    client.post("/api/station/job", json={"job_number": "JOB-K"})                      # next part
    assert herc.ask("threadpresence") == "threadok"
    assert last(client)["decoded_number"] == "JOB-K"      # not a second report for JOB-J
    assert mode(client)["job_number"] is None


def test_ui_check_drops_unpaired_hercules_scan(client, cams, herc):
    assert herc.ask("scanjobno") == PART
    cams["code"] = cv2.imread(str(THREAD))                # next part: code not readable
    cams["thread"] = cv2.imread(str(THREAD))
    assert client.post("/api/inspect", json={}).json()["decoded_number"] is None
    assert herc.ask("threadpresence") == "threadnok"
    assert last(client)["decoded_number"] is None         # never filed under the stale scan


def test_new_part_drops_unpaired_hercules_scan(client, cams, herc):
    cams["thread"] = cv2.imread(str(THREAD))
    assert herc.ask("scanjobno") == PART
    client.post("/api/station/recheck/clear")
    assert herc.ask("threadpresence") == "threadnok"
    assert last(client)["decoded_number"] is None


def test_scan_wins_over_job_typed_after_it(client, cams, herc, bypass):
    """Hercules was told JOB-A; the report must be JOB-A even if the operator types JOB-B before the thread."""
    cams["thread"] = cv2.imread(str(THREAD))
    client.post("/api/station/job", json={"job_number": "JOB-A"})
    assert herc.ask("scanjobno") == "JOB-A"
    client.post("/api/station/job", json={"job_number": "JOB-B"})
    assert herc.ask("threadpresence") == "threadok"
    assert last(client)["decoded_number"] == "JOB-A"
    assert mode(client)["job_number"] == "JOB-B"          # stays for the next part


# ------------------------------------------------------------------ races (UI vs UI vs Hercules)
def _slow_thread_step(monkeypatch):
    orig = R.run_thread_step
    entered, gate = threading.Event(), threading.Event()

    def slow(*a, **k):
        entered.set()
        gate.wait(10)
        return orig(*a, **k)
    monkeypatch.setattr(R, "run_thread_step", slow)
    return entered, gate


def test_new_part_during_running_recheck_is_not_undone(client, cams, monkeypatch):
    client.post("/api/inspect", json={})
    assert mode(client)["recheck"]["part"] == PART
    entered, gate = _slow_thread_step(monkeypatch)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", R.inspect_recheck(R.InspectRequest())))
    t.start()
    assert entered.wait(10)
    cleared = threading.Thread(target=R.station_recheck_clear)
    cleared.start()                                        # waits for the running check
    time.sleep(0.2)
    gate.set()
    t.join(20)
    cleared.join(20)
    assert out["r"]["attempt"] == 2
    assert State.recheck is None                           # New part wins; the old part is not revived


def test_double_recheck_gets_distinct_attempts(client, cams, monkeypatch):
    client.post("/api/inspect", json={})
    entered, gate = _slow_thread_step(monkeypatch)
    res = []
    ts = [threading.Thread(target=lambda: res.append(R.inspect_recheck(R.InspectRequest()))) for _ in range(2)]
    for t in ts:
        t.start()
    time.sleep(0.3)
    gate.set()
    for t in ts:
        t.join(30)
    assert sorted(r["attempt"] for r in res) == [2, 3]
    assert len({r["report_id"] for r in res}) == 2


def test_job_typed_during_bypass_check_survives(client, cams, monkeypatch, bypass):
    entered, gate = _slow_thread_step(monkeypatch)
    t = threading.Thread(target=lambda: R.inspect_online(R.InspectRequest(job_number="JOB-1")))
    t.start()
    assert entered.wait(10)
    typed = threading.Thread(target=lambda: R.station_job(R.JobRequest(job_number="JOB-2")))
    typed.start()
    time.sleep(0.2)
    gate.set()
    t.join(20)
    typed.join(20)
    assert State.job_number == "JOB-2"


# ------------------------------------------------------------------ error paths
def test_check_with_code_camera_missing_keeps_pending(client, cams):
    client.post("/api/inspect", json={})
    cams["code"] = None
    assert client.post("/api/inspect", json={}).status_code == 503
    assert mode(client)["recheck"]["part"] == PART


def test_processing_error_keeps_scanned_part(client, cams, herc, monkeypatch):
    assert herc.ask("scanjobno") == PART

    def boom(*a, **k):
        raise RuntimeError("classifier exploded")
    orig = R.run_thread_step
    monkeypatch.setattr(R, "run_thread_step", boom)
    assert herc.ask("threadpresence") == "error"
    assert mode(client)["recheck"]["part"] == PART and mode(client)["recheck"]["last_state"] == "ERROR"
    monkeypatch.setattr(R, "run_thread_step", orig)
    cams["thread"] = cv2.imread(str(THREAD))
    assert herc.ask("threadpresence") == "threadok"
    r = last(client)
    assert r["decoded_number"] == PART and r["attempt"] == 1  # an error is not a thread check


def test_camera_errors_do_not_count_as_attempts(client, cams, herc):
    assert herc.ask("scanjobno") == PART
    assert herc.ask("threadpresence") == "threadnok"      # attempt 1
    cams["thread"] = None
    assert herc.ask("threadpresence") == "error"
    assert herc.ask("threadpresence") == "error"
    assert mode(client)["recheck"]["attempts"] == 1
    cams["thread"] = cv2.imread(str(THREAD))
    assert herc.ask("threadpresence") == "threadok"
    assert last(client)["attempt"] == 2


def test_recheck_expires(client, cams, monkeypatch):
    client.post("/api/inspect", json={})
    pending = mode(client)["recheck"]
    assert pending and pending["expires_in_s"] > 0
    monkeypatch.setattr(config, "RECHECK_TIMEOUT_MIN", 0.0001)
    time.sleep(0.05)
    assert mode(client)["recheck"] is None
    assert client.post("/api/inspect/recheck", json={}).status_code == 400


# ------------------------------------------------------------------ job numbers as file names
def test_job_numbers_never_share_report_names(tmp_path):
    names = {_safe_part(p) for p in ("A/B", "A.B", "A-B", "A_B")}
    assert len(names) == 4
    for p in ("A/B", "A.B", "A-B", "A_B", "JOB/2026/001", "x~y", "Unread"):
        assert _part_from_name(_safe_part(p)) == p
    writer = ReportWriter(lambda: tmp_path)
    from datetime import datetime
    for p in ("A/B", "A.B", "A-B"):
        writer.write(timestamp=datetime.now(), operator="", mode="online", overall="GO", decoded_number=p,
                     code_result={"passed": True, "text": p}, thread_result={"passed": True, "state": "PRESENT"},
                     code_image=None, thread_image=None)
    for p in ("A/B", "A.B", "A-B"):
        found = writer.by_part(p)
        assert len(found) == 1 and found[0]["decoded_number"] == p


def test_unread_is_not_a_valid_job_number(client, bypass):
    assert client.post("/api/station/job", json={"job_number": "unread"}).status_code == 400


# ------------------------------------------------------------------ banner sizing
def _text_height(out, top, bottom):
    band = out[top:bottom]
    white = (band > 200).all(axis=2)
    rows = np.where(white.any(axis=1))[0]
    return rows.max() - rows.min() if len(rows) else 0


@pytest.mark.parametrize("w,h", [(1280, 720), (2592, 1944), (640, 480), (480, 640), (320, 240)])
def test_banner_scales_with_image_and_fits(w, h):
    img = np.full((h, w, 3), 60, np.uint8)
    out = annotate_code(img, None, CodeOutcome(True, PART, "DataMatrix", "Decoded", None))
    band_top = h - max(60, int(h * 0.22))                 # one-line (14%) or two-line (22%) banner
    white = (out[band_top:] > 200).all(axis=2)
    cols = np.where(white.any(axis=0))[0]
    assert cols.min() > 0 and cols.max() < w - 1          # nothing clipped at the edges
    assert _text_height(out, band_top, h) > h * 0.04      # >= 4% of the image height at every size


def test_not_read_banner_is_red_and_large():
    img = np.full((720, 1280, 3), 60, np.uint8)
    out = annotate_code(img, None, CodeOutcome(False, None, None, "no", None))
    band = out[720 - 100:]
    red = (band[..., 2] > 150) & (band[..., 1] < 90)
    assert red.mean() > 0.4 and _text_height(out, 620, 720) > 720 * 0.045


def test_banner_moves_to_top_when_code_roi_is_at_the_bottom():
    img = np.full((720, 1280, 3), 60, np.uint8)
    roi = (500, 600, 200, 110)
    out = annotate_code(img, roi, CodeOutcome(True, PART, "DataMatrix", "Decoded", roi))
    green_top = ((out[:90, :, 1] > 120) & (out[:90, :, 0] < 110)).mean()
    assert green_top > 0.4                                 # banner at the top
    inside = out[610:700, 510:690]
    assert (np.abs(inside.astype(int) - 60) < 5).mean() > 0.95   # the code area itself is not covered


def test_lookup_by_job_number_with_slash(client, cams, bypass):
    r = client.post("/api/inspect", json={"job_number": "JOB/7.A"}).json()
    assert "JOB~2F7~2EA_" in r["report_id"]
    found = client.get("/api/reports/by-part/JOB%2F7.A").json()
    assert len(found) == 1 and found[0]["decoded_number"] == "JOB/7.A"
    assert client.get("/api/reports/by-part/JOB/7.A").json() == found
