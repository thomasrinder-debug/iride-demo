@echo off
rem iRide Demo einrichten (Windows): sucht ein lauffaehiges Python 3.12+ und startet einrichten.py
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY="
rem Jeder Kandidat muss wirklich starten und mindestens 3.12 sein (py kann auf eine geloeschte Version zeigen,
rem python.exe kann der Microsoft-Store-Platzhalter sein).
for %%K in ("py -3" "python" "python3" "%ProgramFiles%\Python314\python.exe" "%ProgramFiles%\Python313\python.exe" "%ProgramFiles%\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe") do (
  if not defined PY call :pruefen %%K
)
if not defined PY (
  echo Kein Python 3.12 oder neuer gefunden.
  echo Installieren: https://www.python.org/downloads/  ^(Haken bei "Add python.exe to PATH"^)
  echo oder in PowerShell:  winget install Python.Python.3.13
  echo Danach einrichten.cmd erneut starten.
  goto ende
)
%PY% einrichten.py %*
:ende
echo.
pause
exit /b

:pruefen
set "K=%~1"
set "AUFRUF=%K%"
if exist "%K%" set "AUFRUF="%K%""
%AUFRUF% -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul
if %errorlevel%==0 set "PY=%AUFRUF%"
exit /b
