@echo off
REM Starts the Hearth control panel and opens it in your browser.
REM Uses pythonw so no black console window is left behind.
cd /d "%~dp0"

where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw "%~dp0app.py"
  exit /b
)

where python >nul 2>&1
if %errorlevel%==0 (
  start "" python "%~dp0app.py"
  exit /b
)

echo Python was not found on your PATH.
echo Install Python 3.10+ from https://www.python.org/downloads/
echo and make sure "Add Python to PATH" is ticked during setup.
pause
