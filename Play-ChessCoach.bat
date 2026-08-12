@echo off
setlocal
REM ============================================================
REM  The single front door. Double-click this to play.
REM  It runs the local server (one window) and opens your browser.
REM ============================================================
cd /d "%~dp0"

set "VENV=F:\My_Programs\Phenomonological_Chess_Coach_Data\venv\Scripts\python.exe"

if not exist "%VENV%" (
  echo [!] Python venv not found at:
  echo     %VENV%
  echo.
  echo     First-time setup ^(creates the venv on F: and installs deps^):
  echo       pwsh -NoProfile -File "%~dp0tools\Setup-Venv.ps1"
  echo.
  pause
  exit /b 1
)

echo Starting Phenomenological Chess Coach...
"%VENV%" -m app.launcher %*
echo.
echo Server stopped.
pause
