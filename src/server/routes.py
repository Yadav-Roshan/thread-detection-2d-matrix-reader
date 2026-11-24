"""HTTP API (see docs/API.md)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
import threading
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from src.cameras.manager import ROLES, CameraManager, encode_jpeg
from src.core import config
import re

from dataclasses import dataclass, replace as dc_replace

from src.inspection.pipeline import MANUAL_FORMAT, CodeStep, manual_code_step, overall, run_code_step, run_thread_step, to_data_url
from src.inspection.thread_classifier import DetectionParams, ThreadClassifier
from src.outputs.hercules_server import HerculesServer
from src.outputs.pdf_report import ReportWriter
from src.storage.settings_store import SettingsStore, clamp_roi

router = APIRouter()


class State:
    cameras = CameraManager()
    settings = SettingsStore()
    reports = ReportWriter(lambda: State.settings.get_report_directory())
    hercules = HerculesServer()
    last_scan: Optional[CodeStep] = None   # Hercules: last scanjobno result, paired with the next threadpresence
    job_number: Optional[str] = None       # bypass mode: operator-entered number for the next thread check
    recheck: Optional["PendingRecheck"] = None  # part whose thread check was NOK; a re-check keeps its number
    classifier: Optional[ThreadClassifier] = None
    classifier_path: Optional[str] = None
    classifier_lock = threading.Lock()
    # Serialises every inspection AND every change of last_scan / job_number / recheck, so the UI
    # (FastAPI threadpool) and Hercules (asyncio server) can never interleave on the part identity.
    inspect_lock = threading.RLock()
    shutting_down = False


def get_classifier() -> ThreadClassifier:
    with State.classifier_lock:
        if State.classifier is None:
            for candidate in dict.fromkeys((config.MODEL_PATH, config.BUNDLED_MODEL_PATH)):
                try:
                    State.classifier = ThreadClassifier.load(candidate)
                    State.classifier_path = str(candidate)
                    break
                except (FileNotFoundError, ValueError) as exc:  # missing or from an older version
                    logging.getLogger("station.model").warning("Model %s not usable: %s", candidate, exc)
            if State.classifier is None:
                State.classifier = ThreadClassifier.train(config.DATA_DIR)
                State.classifier.save(config.MODEL_SAVE_PATH)
                State.classifier_path = str(config.MODEL_SAVE_PATH)
        return State.classifier


def _role(role: str) -> str:
    if role not in ROLES:
        raise HTTPException(status_code=404, detail=f"Unknown camera role '{role}'")
    return role


# ------------------------------------------------------------------ models
class SelectRequest(BaseModel):
    role: str
    index: int


class ReleaseRequest(BaseModel):
    role: str


class LoginRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class InspectRequest(BaseModel):
    operator: str = ""
    job_number: str = ""


class BypassRequest(BaseModel):
    enabled: bool
    password: str = ""


class JobRequest(BaseModel):
    job_number: str = ""


_JOB_RE = re.compile(r"^[A-Za-z0-9._/\-]{1,64}$")


def _clean_job(value: str) -> Optional[str]:
    job = (value or "").strip()
    if not job:
        return None
    if not _JOB_RE.match(job):
        raise HTTPException(status_code=400, detail="Job number: 1-64 letters, digits, - _ . / only")
    if job.upper() == "UNREAD":
        raise HTTPException(status_code=400, detail="'UNREAD' is reserved for parts whose code was not read")
    return job


def _bypass() -> bool:
    return State.settings.get_bypass_code_reader()


@dataclass
class PendingRecheck:
    """A part whose identity is known but whose thread check was NOK: the next thread check reuses it."""
    code: CodeStep
    attempts: int            # thread checks done so far for this part
    last_state: str
    since: str
    touched: float = 0.0     # monotonic time of the last check (expiry)

    @property
    def part(self) -> str:
        return self.code.decoded_number or ""

    def as_dict(self) -> dict:
        timeout = config.RECHECK_TIMEOUT_MIN * 60
        expires = max(0, int(timeout - (time.monotonic() - self.touched))) if timeout > 0 else None
        return {"part": self.part, "source": "job" if self.code.result.get("format") == MANUAL_FORMAT else "2d",
                "attempts": self.attempts, "last_state": self.last_state, "since": self.since,
                "expires_in_s": expires}

    def expired(self) -> bool:
        timeout = config.RECHECK_TIMEOUT_MIN * 60
        return timeout > 0 and time.monotonic() - self.touched > timeout


def _clear_recheck(reason: str) -> None:
    if State.recheck is not None:
        logging.getLogger("station.recheck").info("re-check for %s cleared (%s)", State.recheck.part, reason)
    State.recheck = None


def _pending_recheck() -> Optional[PendingRecheck]:
    """The part waiting for a re-check, dropping it once it has expired. Call with inspect_lock held."""
    if State.recheck is not None and State.recheck.expired():
        _clear_recheck(f"expired after {config.RECHECK_TIMEOUT_MIN:g} min")
    return State.recheck


def _identified(code: Optional[CodeStep]) -> bool:
    return code is not None and bool(code.result.get("passed")) and bool(code.decoded_number)


def _note_thread_outcome(code: Optional[CodeStep], passed: bool, state: str, attempt: int) -> bool:
    """After a thread check: keep the part for a re-check when NOK, clear it on GO. Returns recheck_pending.
    Call with inspect_lock held."""
    if passed or not _identified(code):
        _clear_recheck("thread OK" if passed else "part identity unknown")
        return False
    State.recheck = PendingRecheck(code=code, attempts=attempt, last_state=state,
                                   since=datetime.now(timezone.utc).isoformat(), touched=time.monotonic())
    logging.getLogger("station.recheck").info("re-check pending for %s (attempt %d %s)", code.decoded_number, attempt, state)
    return True


def _recheck_code(pending: PendingRecheck) -> CodeStep:
    """The stored identity, marked as coming from the earlier read."""
    kept = "job number" if pending.as_dict()["source"] == "job" else "2D code"
    result = {**pending.code.result, "message": f"Thread re-check - {kept} kept from the earlier read (not read again)"}
    return dc_replace(pending.code, result=result)


def _keep_after_error(code: Optional[CodeStep], attempt: int) -> None:
    """The thread check could not run (camera / processing error): keep the part so the check can simply be
    repeated. An error is not a thread check, so the attempt counter does not advance."""
    if not _identified(code):
        return
    if attempt > 1 and State.recheck is not None:
        State.recheck.last_state = "ERROR"
        State.recheck.touched = time.monotonic()
    else:
        _note_thread_outcome(code, False, "ERROR", 0)


def _station_mode() -> dict:
    with State.inspect_lock:
        pending = _pending_recheck()
        return {"bypass_code_reader": _bypass(), "job_number": State.job_number,
                "recheck": pending.as_dict() if pending else None}


# ------------------------------------------------------------------ health
@router.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ------------------------------------------------------------------ cameras
@router.get("/api/camera/devices")
def camera_devices() -> dict:
    return {"devices": State.cameras.discover(), "active": State.cameras.active()}


@router.post("/api/camera/select")
def camera_select(body: SelectRequest) -> dict:
    _role(body.role)
    ok, message = State.cameras.select(body.role, body.index)
    if ok:
        State.settings.set_cameras(State.cameras.active())
    return {"success": ok, "message": message, "active": State.cameras.active()}


@router.post("/api/camera/release")
def camera_release(body: ReleaseRequest) -> dict:
    State.cameras.release(_role(body.role))
    State.settings.set_cameras(State.cameras.active())
    return {"success": True, "active": State.cameras.active()}


@router.get("/api/camera/{role}/status")
def camera_status(role: str) -> dict:
    cam = State.cameras.get(_role(role))
    connected = bool(cam and cam.is_connected())
    width, height = cam.size() if cam else (0, 0)
    return {"connected": connected, "index": cam.index if cam else None, "width": width, "height": height}


@router.get("/api/camera/{role}/stream")
async def camera_stream(role: str, request: Request):
    _role(role)

    async def frames():
        while not State.shutting_down:
            if await request.is_disconnected():
                break
            frame = State.cameras.frame(role)
            if frame is not None:
                payload = await run_in_threadpool(encode_jpeg, frame, 75)
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + payload + b"\r\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


@router.post("/api/camera/{role}/save-frame")
def camera_save_frame(role: str) -> dict:
    frame = State.cameras.frame(_role(role))
    if frame is None:
        raise HTTPException(status_code=503, detail=f"{role} camera is not connected")
    config.FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{role}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png"
    path = config.FRAMES_DIR / filename
    if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 0]):
        raise HTTPException(status_code=500, detail="Failed to write frame image")
    return {"success": True, "path": str(path), "filename": filename}


# ------------------------------------------------------------------ config
@router.get("/api/config/rois")
def get_rois() -> dict:
    return State.settings.get_rois()


@router.post("/api/config/rois")
def set_rois(body: dict) -> dict:
    try:
        return State.settings.set_rois(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/config/detection")
def get_detection() -> dict:
    return State.settings.get_detection().as_dict()


@router.get("/api/config/detection-defaults")
def detection_defaults() -> dict:
    return DetectionParams().as_dict()


@router.post("/api/config/detection")
def set_detection(body: dict) -> dict:
    try:
        return State.settings.set_detection(body).as_dict()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/auth/login")
def login(body: LoginRequest) -> dict:
    if State.settings.check_password(body.password):
        return {"success": True, "message": "Authentication successful"}
    return {"success": False, "message": "Invalid password"}


@router.post("/api/auth/change-password")
def change_password(body: ChangePasswordRequest) -> dict:
    ok, message = State.settings.change_password(body.old_password, body.new_password)
    return {"success": ok, "message": message}


@router.get("/api/model/status")
def model_status() -> dict:
    classifier = get_classifier()
    return {"model_path": State.classifier_path, "metrics": classifier.metrics}


@router.post("/api/model/train")
def model_train() -> dict:
    classifier = ThreadClassifier.train(config.DATA_DIR)
    classifier.save(config.MODEL_SAVE_PATH)
    with State.classifier_lock:
        State.classifier = classifier
        State.classifier_path = str(config.MODEL_SAVE_PATH)
    return {"model_path": str(config.MODEL_SAVE_PATH), "metrics": classifier.metrics}


# ------------------------------------------------------------------ inspection
def _rois_for(frame: np.ndarray, role: str):
    roi = State.settings.get_rois()[role]
    return clamp_roi(roi, frame.shape[1], frame.shape[0])


_RESULT_FILES = {"code": "code_result.jpg", "thread": "thread_result.jpg"}


def _save_result_image(role: str, image: np.ndarray) -> None:
    """Overwrite the single latest annotated result image for this camera (atomic replace)."""
    filename = _RESULT_FILES[role]
    folder = State.settings.get_result_images_directory()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        tmp = folder / (filename + ".tmp")
        tmp.write_bytes(buf.tobytes())
        os.replace(tmp, folder / filename)
    except Exception as exc:  # never block the line because of a result image
        logging.getLogger("station.results").warning("Could not save %s result image: %s", role, exc)


_NOT_SCANNED = {"passed": False, "text": None, "format": None, "message": "2D code not scanned before thread check"}


def _write_report(code: Optional[CodeStep], thread, operator: str, mode: str, timestamp: datetime,
                  attempt: int = 1) -> Optional[str]:
    try:
        return State.reports.write(
            timestamp=timestamp, operator=operator, mode=mode, overall=overall(code, thread), attempt=attempt,
            decoded_number=code.decoded_number if code else None,
            code_result=code.result if code else _NOT_SCANNED,
            thread_result=thread.result,
            code_image=code.image if code else None, thread_image=thread.image)
    except Exception as exc:  # a report failure must never block the line
        logging.getLogger("station.report").exception("PDF generation failed: %s", exc)
        return None


def _payload(code: Optional[CodeStep], thread, operator: str, mode: str, timestamp: datetime,
             report_id: Optional[str], elapsed_ms: Optional[int] = None, attempt: int = 1,
             recheck_pending: bool = False) -> dict:
    return {
        "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
        "attempt": attempt,
        "recheck": attempt > 1,
        "recheck_pending": recheck_pending,
        "elapsed_ms": elapsed_ms,
        "operator": operator,
        "mode": mode,
        "overall_result": overall(code, thread),
        "decoded_number": code.decoded_number if code else None,
        "code_result": code.result if code else _NOT_SCANNED,
        "thread_result": thread.result,
        "code_image": to_data_url(code.image) if code is not None and code.image is not None and attempt == 1 else None,
        "thread_image": to_data_url(thread.image),
        "report_id": report_id,
        "report_url": f"/api/reports/file/{report_id}" if report_id else None,
    }


def _elapsed_ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))


def _run_inspection(code_frame: Optional[np.ndarray], thread_frame: np.ndarray, operator: str, mode: str,
                    started: Optional[float] = None, job_number: Optional[str] = None,
                    use_recheck: bool = False, new_part: bool = False) -> dict:
    """``started`` = perf_counter() when the inspection began (frame capture for live checks).
    With ``job_number`` the 2D reader is bypassed and the number is used as the part identity.
    With ``use_recheck`` only the thread is checked and the kept part identity is reused; the kept part is
    read under the lock, so a "New part" / scan that happened meanwhile is respected.
    ``new_part`` (a normal CHECK) drops any kept part and any unpaired Hercules scan first."""
    started = started if started is not None else time.perf_counter()
    with State.inspect_lock:
        pending = None
        if use_recheck:
            pending = _pending_recheck()
            if pending is None:
                raise HTTPException(status_code=400, detail="No part waiting for a thread re-check")
        if new_part:
            _clear_recheck("new part (CHECK)")
            State.last_scan = None
        attempt = pending.attempts + 1 if pending else 1
        try:
            if pending:
                code = _recheck_code(pending)
            else:
                code = manual_code_step(job_number) if job_number else run_code_step(code_frame, _rois_for(code_frame, "code"))
            thread = run_thread_step(thread_frame, _rois_for(thread_frame, "thread"),
                                     get_classifier(), State.settings.get_detection())
        except ValueError as exc:
            if mode != "offline" and pending:
                _keep_after_error(pending.code, attempt)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            if mode != "offline" and pending:
                _keep_after_error(pending.code, attempt)
            raise
        if code.image is not None and not pending:
            _save_result_image("code", code.image)
        _save_result_image("thread", thread.image)
        now = datetime.now().astimezone()
        report_id = _write_report(code, thread, operator, mode, now, attempt)
        kept = _note_thread_outcome(code, thread.passed, thread.result["state"], attempt) if mode != "offline" else False
        if job_number and State.job_number == job_number:
            State.job_number = None                 # one job number per thread check (a newer one typed meanwhile stays)
    elapsed = _elapsed_ms(started)
    logging.getLogger("station.inspect").info(
        "%s check: part=%s code=%s thread=%s -> %s attempt=%d report=%s time=%dms", mode, code.decoded_number,
        "OK" if code.result["passed"] else "NOREAD", thread.result["state"], overall(code, thread), attempt,
        report_id, elapsed)
    return _payload(code, thread, operator, mode, now, report_id, elapsed, attempt, kept)


@router.post("/api/inspect")
def inspect_online(body: InspectRequest) -> dict:
    started = time.perf_counter()
    if _bypass():
        job = _clean_job(body.job_number) or State.job_number
        if not job:
            raise HTTPException(status_code=400, detail="Enter the job number")
        thread_frame = State.cameras.averaged_frame("thread")
        if thread_frame is None:
            raise HTTPException(status_code=503, detail="thread camera not connected — select it on the feed")
        result = _run_inspection(None, thread_frame, body.operator, "online", started, job_number=job, new_part=True)
        return {**result, "job_number_cleared": True}
    code_frame = State.cameras.frame("code")
    thread_frame = State.cameras.averaged_frame("thread")  # 3-frame mean for repeatability
    missing = [name for name, frame in (("2D code", code_frame), ("thread", thread_frame)) if frame is None]
    if missing:   # nothing is changed (the kept part stays) when a camera is missing
        raise HTTPException(status_code=503,
                            detail=" and ".join(missing) + " camera not connected — select it on the feed")
    return _run_inspection(code_frame, thread_frame, body.operator, "online", started, new_part=True)


@router.post("/api/inspect/recheck")
def inspect_recheck(body: InspectRequest) -> dict:
    """Check the thread again for the part kept after a NOK - the 2D code is not read again."""
    started = time.perf_counter()
    with State.inspect_lock:
        if _pending_recheck() is None:
            raise HTTPException(status_code=400, detail="No part waiting for a thread re-check")
    thread_frame = State.cameras.averaged_frame("thread")
    if thread_frame is None:
        raise HTTPException(status_code=503, detail="thread camera not connected — select it on the feed")
    return _run_inspection(None, thread_frame, body.operator, "online", started, use_recheck=True)


def _decode_upload(raw: bytes, name: str) -> np.ndarray:
    if not raw:
        raise HTTPException(status_code=400, detail=f"{name} is empty")
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail=f"{name} is not a supported image")
    return image


@router.post("/api/inspect/offline")
async def inspect_offline(
    code_image: UploadFile = File(...),
    thread_image: UploadFile = File(...),
    operator: str = Form(""),
) -> dict:
    started = time.perf_counter()
    code_frame = _decode_upload(await code_image.read(), "2D code image")
    thread_frame = _decode_upload(await thread_image.read(), "Thread image")
    return await run_in_threadpool(_run_inspection, code_frame, thread_frame, operator, "offline", started)


# ------------------------------------------------------------------ hercules handlers
def hercules_ready() -> bool:
    roles = ("thread",) if _bypass() else ROLES
    return all(State.cameras.frame(role) is not None for role in roles) and State.classifier is not None


_NO_JOB = {"passed": False, "text": None, "format": MANUAL_FORMAT,
           "message": "No job number entered on the station when Hercules asked (scanjobno replied noread)"}


def hercules_scan() -> tuple[str, dict]:
    cfg = State.hercules.config
    started = time.perf_counter()
    if _bypass():
        with State.inspect_lock:
            _clear_recheck("new part (scanjobno)")
            job = State.job_number
            # A noread is remembered too: the next threadpresence then belongs to an UNIDENTIFIED part and is
            # never filed under a number typed after Hercules was told "noread".
            State.last_scan = (manual_code_step(job) if job
                               else CodeStep(result=dict(_NO_JOB), decoded_number=None, image=None))
            code_result = State.last_scan.result
        result = {"timestamp": datetime.now(timezone.utc).isoformat(), "elapsed_ms": _elapsed_ms(started),
                  "mode": "hercules", "operator": "Hercules", "decoded_number": job,
                  "code_result": code_result, "code_image": None}
        return (job if job else cfg["scan_fail_reply"]), result
    frame = State.cameras.frame("code")
    if frame is None:
        raise RuntimeError("2D code camera not connected")
    with State.inspect_lock:
        _clear_recheck("new part (scanjobno)")
        code = run_code_step(frame, _rois_for(frame, "code"))
        _save_result_image("code", code.image)
        State.last_scan = code
    reply = code.decoded_number if code.result["passed"] else cfg["scan_fail_reply"]
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "elapsed_ms": _elapsed_ms(started),
              "mode": "hercules", "operator": "Hercules",
              "decoded_number": code.decoded_number, "code_result": code.result,
              "code_image": to_data_url(code.image)}
    return reply, result


def _hercules_part() -> tuple[Optional[CodeStep], int]:
    """Which part a threadpresence belongs to (call with inspect_lock held):
    1. the last scanjobno result - exactly what Hercules was told, even "noread";
    2. bypass mode without a scan: the typed job number;
    3. the part kept for a thread re-check (not expired);
    4. otherwise unknown (NO-GO, UNREAD, threadnok)."""
    code, State.last_scan = State.last_scan, None
    if code is not None:
        if _bypass() and code.decoded_number and State.job_number == code.decoded_number:
            State.job_number = None                     # the scanned job number is used up by this check
        return code, 1
    if _bypass() and State.job_number:
        code = manual_code_step(State.job_number)
        State.job_number = None                         # one job number per thread check
        _clear_recheck("new job number")
        return code, 1
    pending = _pending_recheck()
    if pending is not None:
        return _recheck_code(pending), pending.attempts + 1
    return None, 1


def hercules_thread() -> tuple[str, dict]:
    cfg = State.hercules.config
    started = time.perf_counter()
    frame = State.cameras.averaged_frame("thread")
    with State.inspect_lock:
        code, attempt = _hercules_part()
        if frame is None:
            _keep_after_error(code, attempt)            # threadpresence can simply be sent again
            raise RuntimeError("Thread camera not connected")
        try:
            thread = run_thread_step(frame, _rois_for(frame, "thread"), get_classifier(), State.settings.get_detection())
        except Exception:
            _keep_after_error(code, attempt)
            raise
        _save_result_image("thread", thread.image)
        now = datetime.now().astimezone()
        report_id = _write_report(code, thread, "Hercules", "hercules", now, attempt)
        pending = _note_thread_outcome(code, thread.passed, thread.result["state"], attempt)
    # The reply follows the station verdict: threadok only for a GO (identified part AND thread present).
    reply = cfg["thread_ok_reply"] if overall(code, thread) == "GO" else cfg["thread_nok_reply"]
    return reply, _payload(code, thread, "Hercules", "hercules", now, report_id, _elapsed_ms(started),
                           attempt, pending)


State.hercules.set_handlers(ready=hercules_ready, scan=hercules_scan, thread=hercules_thread)


@router.get("/api/station/mode")
def station_mode() -> dict:
    return _station_mode()


@router.post("/api/station/bypass")
def station_bypass(body: BypassRequest) -> dict:
    if not State.settings.check_password(body.password):
        raise HTTPException(status_code=403, detail="Invalid password")
    with State.inspect_lock:
        State.settings.set_bypass_code_reader(body.enabled)
        State.job_number = None
        State.last_scan = None
        _clear_recheck("bypass toggled")
    logging.getLogger("station").info("2D reader bypass %s", "ON" if body.enabled else "OFF")
    return _station_mode()


@router.post("/api/station/job")
def station_job(body: JobRequest) -> dict:
    if not _bypass():
        raise HTTPException(status_code=400, detail="Job number entry is only used when the 2D reader is bypassed")
    job = _clean_job(body.job_number)
    with State.inspect_lock:
        if job and job != State.job_number:
            _clear_recheck("new job number")
        State.job_number = job
    return _station_mode()


@router.post("/api/station/recheck/clear")
def station_recheck_clear() -> dict:
    with State.inspect_lock:
        _clear_recheck("operator pressed New part")
        State.last_scan = None                          # an unpaired Hercules scan belongs to the old part too
    return _station_mode()


@router.get("/api/config/hercules")
def get_hercules_config() -> dict:
    return State.hercules.config


@router.post("/api/config/hercules")
async def set_hercules_config(body: dict) -> dict:
    try:
        cfg = State.settings.set_hercules({**State.hercules.config, **body})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await State.hercules.apply_config(cfg)


@router.get("/api/hercules/status")
def hercules_status() -> dict:
    return State.hercules.status()


# ------------------------------------------------------------------ reports (PDF)
class DirectoryRequest(BaseModel):
    directory: Optional[str] = None


@router.get("/api/report/directory")
def get_report_directory() -> dict:
    return {"directory": str(State.settings.get_report_directory()), "default": str(config.PDF_DIR),
            "structure": "<directory>/<YYYY>/<MM>/<DD>/<GO|NO_GO>/<part>_<HHMMSS>.pdf"}


@router.post("/api/report/set-directory")
def set_report_directory(body: DirectoryRequest) -> dict:
    try:
        path = State.settings.set_report_directory(body.directory)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "directory": str(path)}


def _browse_folder(initial: str, title: str = "Select report folder") -> Optional[str]:
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askdirectory(parent=root, initialdir=initial, mustexist=False,
                                         title=title)
    finally:
        root.destroy()
    return chosen or None


@router.post("/api/report/browse-directory")
def browse_report_directory() -> dict:
    """Open the native folder picker on the station PC (the server runs on the same machine)."""
    try:
        chosen = _browse_folder(str(State.settings.get_report_directory()))
    except Exception as exc:
        raise HTTPException(status_code=501,
                            detail=f"Folder picker unavailable here, type the path instead ({exc})") from exc
    return {"directory": chosen}


@router.get("/api/reports")
def reports(limit: int = 50) -> list[dict]:
    return State.reports.recent(limit)


@router.get("/api/reports/by-part/{decoded_number:path}")  # job numbers may contain "/"
def reports_by_part(decoded_number: str) -> list[dict]:
    return State.reports.by_part(decoded_number)


@router.get("/api/reports/file/{report_id:path}")
def report_file(report_id: str):
    try:
        path = State.reports.resolve(report_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Report not found") from exc
    return FileResponse(path, media_type="application/pdf", filename=path.name,
                        content_disposition_type="inline")


# ------------------------------------------------------------------ result images folder
@router.get("/api/result-images/directory")
def get_result_images_directory() -> dict:
    return {"directory": str(State.settings.get_result_images_directory()),
            "default": str(config.RESULT_IMAGES_DIR),
            "files": list(_RESULT_FILES.values())}


@router.post("/api/result-images/set-directory")
def set_result_images_directory(body: DirectoryRequest) -> dict:
    try:
        path = State.settings.set_result_images_directory(body.directory)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "directory": str(path)}


@router.post("/api/result-images/browse-directory")
def browse_result_images_directory() -> dict:
    try:
        chosen = _browse_folder(str(State.settings.get_result_images_directory()), "Select result images folder")
    except Exception as exc:
        raise HTTPException(status_code=501,
                            detail=f"Folder picker unavailable here, type the path instead ({exc})") from exc
    return {"directory": chosen}
