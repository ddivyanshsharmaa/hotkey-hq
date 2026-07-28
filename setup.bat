@echo off
setlocal
echo Setting up Hotkey HQ...
echo.

rem Find a usable Python 3.
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo Python 3 was not found on this PC.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
  echo then run this file again.
  pause
  exit /b 1
)

echo Creating a private environment in .venv ...
%PY% -m venv "%~dp0.venv" || goto :failed

echo Installing pywebview ...
"%~dp0.venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%~dp0.venv\Scripts\python.exe" -m pip install --quiet -r "%~dp0requirements.txt" || goto :failed

rem Prove the window toolkit actually imports, rather than finding out later in
rem pythonw.exe where nothing is visible.
"%~dp0.venv\Scripts\python.exe" -c "import webview" || goto :failed

echo.
echo Done. Start it with launch.bat
pause
exit /b 0

:failed
echo.
echo Setup failed. Check the messages above.
pause
exit /b 1
