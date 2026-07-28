@echo off
rem %~dp0 is this script's own folder, so Hotkey HQ runs from wherever it lives.
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo First run: setting up. Please run setup.bat once.
  pause
  exit /b 1
)
start "Hotkey HQ" /min "%~dp0.venv\Scripts\pythonw.exe" "%~dp0server.py"
