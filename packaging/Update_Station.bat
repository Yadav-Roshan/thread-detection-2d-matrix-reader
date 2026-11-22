@echo off
REM Update the CamShaft Inspection Station in this folder to the latest release.
REM Optional: drag a release zip onto this file (or pass its path) to update offline.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update_Station.ps1" %*
if errorlevel 1 (echo. & echo Update failed - the station was not changed if the error happened before "Installing". & pause)
