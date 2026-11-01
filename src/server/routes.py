"""HTTP API (see docs/API.md)."""
from __future__ import annotations

import asyncio
import logging
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
from src.inspection.pipeline import CodeStep, overall, run_code_step, run_thread_step, to_data_url
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
    last_scan: Optional[CodeStep] = None   # Hercules: last scanjobno result, paired with next threadpresence
    classifier: Optional[ThreadClassifier] = None
    classifier_lock = threading.Lock()
    inspect_lock = threading.Lock()
    shutting_down = False


def get_classifier() -> ThreadClassifier:
    with State.classifier_lock:
        if State.classifier is None:
            try:
                State.classifier = ThreadClassifier.load(config.MODEL_PATH)
            except (FileNotFoundError, ValueError):
                State.classifier = ThreadClassifier.train(config.DATA_DIR)
                State.classifier.save(config.MODEL_SAVE_PATH)
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
    return {"model_path": str(config.MODEL_PATH), "metrics": get_classifier().metrics}


@router.post("/api/model/train")
def model_train() -> dict:
    classifier = ThreadClassifier.train(config.DATA_DIR)
    classifier.save(config.MODEL_SAVE_PATH)
    with State.classifier_lock:
        State.classifier = classifier
    return {"model_path": str(config.MODEL_SAVE_PATH), "metrics": classifier.metrics}


# ------------------------------------------------------------------ inspection
def _rois_for(frame: np.ndarray, role: str):
    roi = State.settings.get_rois()[role]
    return clamp_roi(roi, frame.shape[1], frame.shape[0])


_NOT_SCANNED = {"passed": False, "text": None, "format": None, "message": "2D code not scanned before thread check"}


def _write_report(code: Optional[CodeStep], thread, operator: str, mode: str, timestamp: datetime) -> Optional[str]:
    try:
        return State.reports.write(
            timestamp=timestamp, operator=operator, mode=mode, overall=overall(code, thread),
            decoded_number=code.decoded_number if code else None,
            code_result=code.result if code else _NOT_SCANNED,
            thread_result=thread.result,
            code_image=code.image if code else None, thread_image=thread.image)
    except Exception as exc:  # a report failure must never block the line
        logging.getLogger("station.report").exception("PDF generation failed: %s", exc)
        return None


def _payload(code: Optional[CodeStep], thread, operator: str, mode: str, timestamp: datetime,
             report_id: Optional[str]) -> dict:
    return {
        "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
        "operator": operator,
        "mode": mode,
        "overall_result": overall(code, thread),
        "decoded_number": code.decoded_number if code else None,
        "code_result": code.result if code else _NOT_SCANNED,
        "thread_result": thread.result,
        "code_image": to_data_url(code.image) if code else None,
        "thread_image": to_data_url(thread.image),
        "report_id": report_id,
        "report_url": f"/api/reports/file/{report_id}" if report_id else None,
    }


def _run_inspection(code_frame: np.ndarray, thread_frame: np.ndarray, operator: str, mode: str) -> dict:
    with State.inspect_lock:
        try:
            code = run_code_step(code_frame, _rois_for(code_frame, "code"))
            thread = run_thread_step(thread_frame, _rois_for(thread_frame, "thread"),
                                     get_classifier(), State.settings.get_detection())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        now = datetime.now().astimezone()
        report_id = _write_report(code, thread, operator, mode, now)
    logging.getLogger("station.inspect").info(
        "%s check: part=%s code=%s thread=%s -> %s report=%s", mode, code.decoded_number,
        "OK" if code.result["passed"] else "NOREAD", thread.result["state"], overall(code, thread), report_id)
    return _payload(code, thread, operator, mode, now, report_id)


@router.post("/api/inspect")
def inspect_online(body: InspectRequest) -> dict:
    code_frame = State.cameras.frame("code")
    thread_frame = State.cameras.averaged_frame("thread")  # 3-frame mean for repeatability
    missing = [name for name, frame in (("2D code", code_frame), ("thread", thread_frame)) if frame is None]
    if missing:
        raise HTTPException(status_code=503,
                            detail=" and ".join(missing) + " camera not connected — select it on the feed")
    return _run_inspection(code_frame, thread_frame, body.operator, "online")


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
    code_frame = _decode_upload(await code_image.read(), "2D code image")
    thread_frame = _decode_upload(await thread_image.read(), "Thread image")
    return await run_in_threadpool(_run_inspection, code_frame, thread_frame, operator, "offline")


# ------------------------------------------------------------------ hercules handlers
def hercules_ready() -> bool:
    return all(State.cameras.frame(role) is not None for role in ROLES) and State.classifier is not None


def hercules_scan() -> tuple[str, dict]:
    cfg = State.hercules.config
    frame = State.cameras.frame("code")
    if frame is None:
        raise RuntimeError("2D code camera not connected")
    with State.inspect_lock:
        code = run_code_step(frame, _rois_for(frame, "code"))
    State.last_scan = code
    reply = code.decoded_number if code.result["passed"] else cfg["scan_fail_reply"]
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "mode": "hercules", "operator": "Hercules",
              "decoded_number": code.decoded_number, "code_result": code.result,
              "code_image": to_data_url(code.image)}
    return reply, result


def hercules_thread() -> tuple[str, dict]:
    cfg = State.hercules.config
    frame = State.cameras.averaged_frame("thread")
    if frame is None:
        raise RuntimeError("Thread camera not connected")
    with State.inspect_lock:
        thread = run_thread_step(frame, _rois_for(frame, "thread"), get_classifier(), State.settings.get_detection())
        code, State.last_scan = State.last_scan, None   # one scan pairs with one thread check
        now = datetime.now().astimezone()
        report_id = _write_report(code, thread, "Hercules", "hercules", now)
    reply = cfg["thread_ok_reply"] if thread.passed else cfg["thread_nok_reply"]
    return reply, _payload(code, thread, "Hercules", "hercules", now, report_id)


State.hercules.set_handlers(ready=hercules_ready, scan=hercules_scan, thread=hercules_thread)


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


def _browse_folder(initial: str) -> Optional[str]:
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askdirectory(parent=root, initialdir=initial, mustexist=False,
                                         title="Select report folder")
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


@router.get("/api/reports/by-part/{decoded_number}")
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
