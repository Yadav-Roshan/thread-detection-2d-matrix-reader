# CamShaft 2D Matrix & Thread Inspection — API contract

Backend: FastAPI on `http://127.0.0.1:8000`, serves the static Next.js export from `frontend/out`.
All paths are same-origin (frontend uses relative URLs via `apiUrl()`).

Two camera roles: `code` (2D Data Matrix camera) and `thread` (thread bore camera).
ROI = `{ "x": int, "y": int, "width": int, "height": int }` in **natural image pixels**.

## Cameras (server-side OpenCV, like cut-mark-station)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/camera/devices` | – | `{ devices: [{index, label, width, height}], active: {code: int\|null, thread: int\|null} }` |
| POST | `/api/camera/select` | `{ role: "code"\|"thread", index: int }` | `{ success: bool, message: str, active: {...} }` |
| POST | `/api/camera/release` | `{ role }` | `{ success, active }` |
| GET | `/api/camera/{role}/stream` | – | MJPEG `multipart/x-mixed-replace` (use as `<img src>`) |
| GET | `/api/camera/{role}/status` | – | `{ connected: bool, index: int\|null, width: int, height: int }` |
| POST | `/api/camera/{role}/save-frame` | – | `{ success, path, filename }` |

## Configuration (SETUP)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/config/rois` | – | `{ code: ROI\|null, thread: ROI\|null }` |
| POST | `/api/config/rois` | `{ code: ROI\|null, thread: ROI\|null }` | same as GET |
| GET | `/api/config/detection` | – | `DetectionParams` |
| POST | `/api/config/detection` | `DetectionParams` | `DetectionParams` |
| GET | `/api/config/detection-defaults` | – | `DetectionParams` |
| POST | `/api/auth/login` | `{ password }` | `{ success, message }` |
| POST | `/api/auth/change-password` | `{ old_password, new_password }` | `{ success, message }` |

`DetectionParams`:
```json
{
  "min_ridge_area": 75000,      // native px² of tangential thread ridges needed for PRESENT
  "min_ridge_band_px": 180,     // radial depth of the ridge zone (far view → small)
  "min_purity": 0.4,            // share of fine texture that is thread-ordered (0–1)
  "min_classifier_margin": 0.25, // SVM margin needed for PRESENT
  "max_novelty": 2.0           // reject views unlike training bores (hand, wrong scene)
}
```

## Inspection

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/inspect` | JSON `{ operator: str }` — LIVE: grabs a frame from both server cameras, uses saved ROIs, writes PDF | `InspectionResult` |
| POST | `/api/inspect/offline` | multipart `code_image`, `thread_image`, `operator` (SETUP → Offline test; saved ROIs applied; writes PDF) | `InspectionResult` |

`InspectionResult`:
```json
{
  "timestamp": "ISO-8601",
  "operator": "Ravi",
  "mode": "online" | "offline" | "hercules",
  "overall_result": "GO" | "NO_GO",
  "decoded_number": "ABC123" | null,
  "code_result": { "passed": true, "text": "ABC123", "format": "DataMatrix", "message": "Decoded" },
  "thread_result": {
    "passed": true,
    "state": "PRESENT" | "ABSENT" | "NOT_VISIBLE",
    "confidence": 0.97,
    "message": "...",
    "evidence": { "ridge_area": 120000, "ridge_band_px": 300, "purity": 0.7, "symmetry": 8, "coverage": 1, "classifier_margin": 1.2, "novelty": 0.6 }
  },
  "code_image": "data:image/jpeg;base64,...",   // full-frame annotated capture
  "thread_image": "data:image/jpeg;base64,...", // full-frame annotated capture
  "report_id": "2026/11/01/GO/ABC123_130405.pdf",   // null if PDF failed
  "report_url": "/api/reports/file/2026/11/01/GO/ABC123_130405.pdf"
}
```
GO only if the code decoded **and** thread state is PRESENT. A failed decode is still reported (decoded_number `null`) and is NO_GO.
Errors: 400 bad input, 503 when a required camera isn't connected (`detail` explains which).

## Reports (PDF files — no database)

Same scheme as cut-mark-station — date-wise, then verdict-wise:
`<report directory>/<YYYY>/<MM>/<DD>/<GO|NO_GO>/<part>_<HHMMSS>.pdf` (part = decoded 2D number, `UNREAD` if not read).
The report directory is chosen in SETUP → Reports.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/reports?limit=50` | – | `ReportRecord[]` newest first (scans the current report directory) |
| GET | `/api/reports/by-part/{decoded_number}` | – | `ReportRecord[]` |
| GET | `/api/reports/file/{report_id}` | – | the PDF inline (`application/pdf`, open in new tab) |
| GET | `/api/report/directory` | – | `{ directory, default, structure }` |
| POST | `/api/report/set-directory` | `{ directory: str \| null }` (null/"" = default) | `{ success, directory }` · 400 with `detail` if not absolute / not writable |
| POST | `/api/report/browse-directory` | – | `{ directory: str \| null }` — opens the native Windows folder picker **on the station PC** (blocks until the user picks/cancels; null = cancelled). Does NOT save — UI fills the input, user clicks Save. 501 if unavailable. |

`report_id` example: `2026/11/01/GO/005715051079230AP666711026002315_130405.pdf`

`ReportRecord`: `{ report_id, timestamp, decoded_number (null if unread), overall_result, report_url }`

## Hercules (TCP server; Hercules connects as client)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/config/hercules` | – | `HerculesConfig` |
| POST | `/api/config/hercules` | `HerculesConfig` | `HerculesConfig` (server restarts listener) |
| GET | `/api/hercules/status` | – | `HerculesStatus` (poll ~1 s) |

`HerculesConfig` (all strings editable in SETUP → Hercules):
```json
{
  "enabled": false, "bind_host": "0.0.0.0", "port": 9000,
  "ready_expect": "isready",        "ready_reply": "readyyes",  "not_ready_reply": "readyno",
  "scan_expect": "scanjobno",       "scan_fail_reply": "noread",      // success reply = decoded 2D number
  "thread_expect": "threadpresence", "thread_ok_reply": "threadok", "thread_nok_reply": "threadnok",
  "error_reply": "error"
}
```
Flow: Hercules `isready` → `readyyes` (both cameras live) / `readyno`; `scanjobno` → decoded number / `noread`;
`threadpresence` → `threadok` / `threadnok` and a PDF report combining the last scanned number with the thread result.

`HerculesStatus`:
```json
{
  "enabled": true, "listening": true, "bind_host": "0.0.0.0", "port": 9000,
  "client_connected": true, "client_addr": "192.168.1.5:50122", "last_error": null,
  "last_event": {
    "seq": 7, "kind": "connect" | "disconnect" | "ready" | "scan" | "thread" | "error",
    "message": "scanjobno", "reply": "ABC123", "timestamp": "ISO-8601",
    "result": InspectionResult | null   // scan: code_result+code_image (+decoded_number); thread: full result incl. report_url
  } | null
}
```
The INSPECTION screen should poll this and, when `seq` changes with a `scan`/`thread` result, show it exactly like a manual CHECK.

## Misc
`GET /api/health` → `{ status: "ok" }`
