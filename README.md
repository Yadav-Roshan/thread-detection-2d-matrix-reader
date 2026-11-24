# CamShaft 2D Matrix & Thread Inspection

Shop-floor POKA YOKE station (FastAPI + OpenCV + Next.js), styled and structured like `cut-mark-station`:

1. Read the Data Matrix on the camshaft (camera 01).
2. Verify the tapped hole has thread, using a borescope (camera 02).
3. GO only if the code is read **and** thread is PRESENT. Every inspection writes a **PDF report** (no database).

## Run

```powershell
uv sync
cd frontend; npm install; npm run build; cd ..
uv run python main.py          # http://127.0.0.1:8000  (PORT env var to change)
```

Camera assignments, ROIs, detection settings, Hercules settings and the report folder are saved under `config/`
and restored at startup.

## Operation

- **INSPECTION** (live cameras only): pick each camera on its feed once, then press **CHECK** (or Enter),
  or let Hercules drive the station.
- **SETUP** (password, same as cut-mark-station):
  - **ROIs** (optional; without them the full frame is used)
  - **Offline test** (upload a code image and a thread image)
  - **Reports** (choose the report folder and look up PDFs by part number)
  - **Detection** gates
  - **Hercules** link

## Latest result images

Every run overwrites one annotated image per camera beside the app:
`result_images\code_result.jpg` and `result_images\thread_result.jpg` (no history kept). The folder can be changed in SETUP → Reports.

## Bypass 2D reader (job number)

A switch on the INSPECTION screen (SETUP password needed to turn it on or off; remembered across restarts).
When on, the 2D code camera is not used: the operator types a job number for each part, CHECK verifies the
thread only, the report is filed under the job number, and the number is cleared after every thread check.
Hercules: `isready` needs only the thread camera, `scanjobno` replies with the typed job number (`noread` if
none), `threadpresence` checks the thread and clears the job number.

## Thread re-check (keep the part number)

If the 2D code was read (or a job number entered) but the thread check is NOK, the station keeps the part
number. Checking the thread again does **not** read the 2D code again: Hercules simply sends `threadpresence`
again, or the operator presses **Re-check thread** (shortcut `R`). Each attempt gets its own PDF with the attempt
number. The kept part is dropped on GO, when a new part starts (`scanjobno`, CHECK, a new job number) or with
**New part**. Enter/Space always run a normal CHECK, never a re-check.

## Hercules (station = TCP server, Hercules = client)

| Hercules sends | Station replies |
|---|---|
| `isready` | `readyyes` (both cameras live) / `readyno` |
| `scanjobno` | the decoded 2D number / `noread` |
| `threadpresence` | `threadok` / `threadnok`, and writes the PDF report (last scanned number + thread result) |
| anything else | `error` |

All strings, the listen address and the port can be edited in SETUP → Hercules. Default port is 9000.

## Reports

PDFs use the same date-wise, verdict-wise layout as cut-mark-station:

```
<report folder>\YYYY\MM\DD\GO|NO_GO\<2D number>_<HHMMSS>.pdf     (UNREAD_<HHMMSS>.pdf if the code wasn't read)
```

Choose the folder in SETUP → Reports. **Browse…** opens the Windows folder picker on the station PC; you can
also type a path. The default folder is `reports\pdf`. The logo is `assets\logo.jpeg`.

## Thread decision (fail-safe)

`src/inspection/thread_evidence.py` measures thread crests: fine ridges running tangentially around the bore.
`src/inspection/thread_classifier.py` reports PRESENT only when all three gates pass:

- **novelty:** rejects a hand or obstruction in view, or the wrong scene
- **visibility:** rejects a borescope that is too far away, defocus, or partial obstruction
- **SVM margin:** rejects a plain drilled bore

Otherwise the result is ABSENT or NOT_VISIBLE, and both are NO-GO.

Cross-validated on 117 images (PRESENT = positive): accuracy 96.6%, precision 100% (0 of 32 thread-less
bores accepted, no false GO), recall 95.3% (81 of 85 threads; the rest are safe NO-GO re-checks).
Part faces seen from outside (the 2D-Code images, each held out in turn), hand / far / room scenes are all
rejected. Frames that must never pass can be added to `data/Reject` and the model retrained.

## Windows executable (production)

```powershell
.\scripts\build_executable.ps1
```

This builds the UI, runs PyInstaller (onedir layout, same as cut-mark-station) and produces:

- `dist\CamShaft_Inspection_Station\CamShaft_Inspection_Station.exe`
- `dist\CamShaft_Inspection_Station-win64.zip` (about 140 MB)

To install, copy and extract the zip on the station PC and double-click the exe. The browser opens by itself.
Keep the whole folder together (`_internal\` is required). The `config\`, `reports\` and `logs\` folders are
created beside the exe. An optional `.env` beside the exe can override `PORT`, `HOST`, `OPEN_BROWSER` and
`CAMERA_PROBE_MAX`. Operator notes are in `packaging\OPERATOR_README.txt`, shipped as `README.txt`.

## Updating a station

Put the single file `Update_Station.bat` in the station folder (next to the exe) and double-click it. It downloads
the latest release from GitHub (offline PC: copy the release zip into the folder first), stops the station,
replaces only the program files and restarts it. `config`, `reports`, `logs`, `result_images`, retrained models
and data are kept; the previous version is kept in `_previous_version` and is restored automatically if the
install fails. The window stays open with the result; details are in `Update_Station.log`. It is not blocked by
PC script-execution policies (the PowerShell part is embedded and loaded as text) and uses the system proxy.
Source: `packaging/Update_Station.ps1`, packed into the .bat by `scripts/make_updater.py`.

## Tests

```powershell
uv run pytest -q              # backend incl. PDF + Hercules TCP protocol
cd frontend; npm run test:e2e # UI (mocked API)
```

See `docs/API.md` for the HTTP API.
