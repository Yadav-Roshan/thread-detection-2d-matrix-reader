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
        threadpresence -> threadok / threadnok   (PDF report is written; threadok only for GO,
                          so an unidentified part always gets threadnok)
  4. ROIs are optional. Without them the full camera frame is used.

FOLDERS CREATED BESIDE THE EXE
  config\   settings (cameras, ROIs, Hercules, report folder, password)
  reports\  default PDF report folder
  logs\     station.log (send this file when reporting a problem)
  frames\   frames saved with the save button on a feed
  result_images\code_result.jpg   latest annotated 2D-code image (overwritten every run)
  result_images\thread_result.jpg   latest annotated thread image (overwritten every run)

Optional .env beside the exe: PORT=8000, HOST=127.0.0.1, OPEN_BROWSER=1, CAMERA_PROBE_MAX=6,
  RECHECK_TIMEOUT_MIN=15 (minutes a kept part waits for a re-check; 0 = never expire)

UPDATING TO A NEW VERSION
  Double-click Update_Station.bat in this folder (it is a single self-contained file). It downloads the latest release, stops the
  station, replaces only the program files and starts it again. Settings (config), reports,
  logs, result images and retrained models are kept; the old version is kept in
  _previous_version. Offline: drag a downloaded release zip onto Update_Station.bat.

TEACHING THE STATION A FALSE "THREAD PRESENT" SCENE
  Save the borescope frame (save button on the feed) into data\Reject and retrain the model.

THREAD RE-CHECK (2D CODE ALREADY READ, THREAD NOK)
  After a thread NOK the part number is kept and an amber "Thread re-check pending" card appears.
  Re-check thread (or key R) checks only the thread again - the 2D code is not read again.
  Hercules: just send threadpresence again. Each attempt has its own PDF ("Attempt 2 (thread re-check)").
  New part (or CHECK / scanjobno / a new job number) drops the kept part. It is also dropped after
  15 minutes without a check. Keys: R = re-check this part, Enter = CHECK a new part.
  Bypass mode: after a NOK the cursor leaves the job number box, so R re-checks (it does not type "r").
