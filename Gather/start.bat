@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Sports Elective Sign-up Server

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul
if errorlevel 1 (
  echo.
  echo  [ERROR] Python 3.8+ is required but was not found.
  echo          Install it from https://www.python.org/downloads/
  echo          and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

%PY% app.py %*
echo.
echo  Server stopped.
pause
