"""Build the single-file updater: packaging/Update_Station.bat = batch launcher + embedded PowerShell.

The batch part reads its own file and runs everything after the marker line with Invoke-Expression,
so the station needs only this one file, and PC script-execution policies do not block it.
Source of the PowerShell logic: packaging/Update_Station.ps1.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "#==POWERSHELL==#"

HEAD = r"""@echo off
setlocal
REM ============================================================================
REM  CamShaft Inspection Station - updater (single file, keep it in the station
REM  folder next to CamShaft_Inspection_Station.exe and double-click it).
REM  It downloads the latest release from GitHub and replaces the program files.
REM  Kept: config, reports, logs, frames, result_images, retrained models, data.
REM  Offline PC: copy CamShaft_Inspection_Station-win64.zip into this folder
REM  (or drag the zip onto this file) and run it.
REM ============================================================================
title CamShaft Station Updater
cd /d "%~dp0"
set "CAMSHAFT_APP_DIR=%~dp0"
set "CAMSHAFT_UPDATE_ZIP=%~1"
set "CAMSHAFT_SELF=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t = [IO.File]::ReadAllText($env:CAMSHAFT_SELF); $i = $t.LastIndexOf('#==POWERSHELL' + '==#'); Invoke-Expression $t.Substring($i)"
if errorlevel 1 (
  echo.
  echo Update FAILED. See the message above and Update_Station.log in this folder.
) else (
  echo.
  echo Update finished.
)
echo.
pause
exit /b
"""


def build() -> Path:
    ps = (ROOT / "packaging" / "Update_Station.ps1").read_text(encoding="utf-8")
    assert MARKER not in ps, "marker must not appear in the PowerShell source"
    text = HEAD + MARKER + "\n" + ps
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("ascii")  # cmd.exe and Windows PowerShell 5.1: pure ASCII, CRLF
    out = ROOT / "packaging" / "Update_Station.bat"
    out.write_bytes(data)
    return out


if __name__ == "__main__":
    print(build())
