CamShaft 2D Matrix & Thread Inspection Station
===============================================

START
  Double-click CamShaft_Inspection_Station.exe. The browser opens http://127.0.0.1:8000 automatically.
  Keep the black console window open while working (it is the station server). Closing it stops the station.
  Keep this whole folder together. The _internal folder is required.

FIRST-TIME SETUP (SETUP tab, password protected; same password as Cut Mark Station)
  1. On each camera feed, choose the camera from its dropdown: 01 = 2D code camera, 02 = thread borescope.
     Choices are remembered.
  2. Reports tab: choose the report folder (Browse...). PDFs are saved as
        <folder>\YYYY\MM\DD\GO or NO_GO\<2D number>_<HHMMSS>.pdf
  3. Hercules tab: enable "Accept Hercules connections", set port (default 9000), check strings, Save.
        isready        -> readyyes / readyno
        scanjobno      -> <2D number> / noread
        threadpresence -> threadok / threadnok   (PDF report is written)
  4. ROIs are optional. Without them the full camera frame is used.

FOLDERS CREATED BESIDE THE EXE
  config\   settings (cameras, ROIs, Hercules, report folder, password)
  reports\  default PDF report folder
  logs\     station.log (send this file when reporting a problem)
  frames\   frames saved with the save button on a feed

Optional .env beside the exe: PORT=8000, HOST=127.0.0.1, OPEN_BROWSER=1, CAMERA_PROBE_MAX=6
