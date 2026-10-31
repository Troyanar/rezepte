@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Python wurde nicht gefunden. Bitte Python 3 installieren: https://www.python.org/downloads/
    pause
    exit /b 1
)
if not exist .venv\Scripts\python.exe (
    echo Richte Python-Umgebung ein ...
    python -m venv .venv
    if errorlevel 1 goto fehler
)
rem Pakete jedes Mal pruefen: geht schnell, und nach einem abgebrochenen ersten Start fehlt sonst etwas
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fehler
.venv\Scripts\python set_password.py --if-missing
.venv\Scripts\python app.py
pause
exit /b 0

:fehler
echo.
echo Einrichtung fehlgeschlagen (Internet da?). Beim naechsten Start wird es erneut versucht.
pause
exit /b 1
