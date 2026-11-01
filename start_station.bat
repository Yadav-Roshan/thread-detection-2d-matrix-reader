@echo off
REM CamShaft 2D Matrix & Thread Inspection - production launcher
cd /d "%~dp0"
where uv >nul 2>nul || (echo uv not found. Install uv first: https://docs.astral.sh/uv/ & pause & exit /b 1)
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start http://127.0.0.1:8000"
uv run --no-dev python main.py
pause
