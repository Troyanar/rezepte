@echo off
chcp 65001 >nul
title TikTok-Rezepte importieren
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Die Python-Umgebung fehlt noch. Bitte erst einmal start.bat ausfuehren.
  pause
  exit /b 1
)

set "datei=%~1"
if not "%datei%"=="" goto los
echo TXT-Datei vom TikTok-Bot hier reinziehen und Enter druecken
echo (oder den Pfad eintippen):
set /p "datei=> "
set "datei=%datei:"=%"

:los
if not exist "%datei%" (
  echo Datei nicht gefunden: %datei%
  pause
  exit /b 1
)
echo.
.venv\Scripts\python import_tiktok.py "%datei%"
echo.
pause