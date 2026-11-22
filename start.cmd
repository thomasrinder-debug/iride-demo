@echo off
rem iRide Demo starten (Windows): Server aus .venv, Seite in Chrome
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Noch nicht eingerichtet - bitte zuerst einrichten.cmd starten.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" iride_server.py --oeffnen %*
pause
