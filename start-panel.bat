@echo off
setlocal enabledelayedexpansion
REM ---------------------------------------------------------------------------
REM  Hearth launcher.
REM
REM  Most "Python was not found" problems are not a missing Python at all - it
REM  is installed, but the "Add Python to PATH" box was never ticked, so the
REM  plain `python` command does nothing. This looks in every normal place
REM  before giving up, and only sends you to the website if Python genuinely
REM  is not on this PC.
REM
REM  pythonw is preferred over python so no black console window is left behind.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
set "PY="

REM 1. the py launcher - installed with Python by default, works without PATH
where pyw >nul 2>&1 && (set "PY=pyw -3" & goto :run)
where py  >nul 2>&1 && (set "PY=py -3"  & goto :run)

REM 2. on PATH
where pythonw >nul 2>&1 && (set "PY=pythonw" & goto :run)
where python  >nul 2>&1 && (set "PY=python"  & goto :run)

REM 3. the usual install folders, newest first
for %%R in (
  "%LocalAppData%\Programs\Python"
  "%ProgramFiles%\Python"
  "%ProgramFiles(x86)%\Python"
  "C:\"
) do (
  if exist "%%~R" (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%%~R\Python3*" 2^>nul') do (
      if exist "%%~R\%%D\pythonw.exe" (set "PY="%%~R\%%D\pythonw.exe"" & goto :run)
      if exist "%%~R\%%D\python.exe"  (set "PY="%%~R\%%D\python.exe""  & goto :run)
    )
  )
)

REM 4. wherever the Microsoft Store put it
if exist "%LocalAppData%\Microsoft\WindowsApps\python.exe" (
  set "PY="%LocalAppData%\Microsoft\WindowsApps\python.exe"" & goto :run
)

REM ------------------------------------------------------------------ no luck
echo.
echo   Hearth needs Python, and it is not on this PC.
echo.
echo   1. Go to  https://www.python.org/downloads/
echo   2. Download the big yellow "Download Python" button.
echo   3. Run it, and TICK the box that says "Add python.exe to PATH".
echo   4. Double-click this file again.
echo.
echo   It is a normal, safe install and takes about two minutes.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:run
start "" %PY% "%~dp0app.py"
exit /b 0
