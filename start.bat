@echo off
setlocal
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" (
  start "" "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" "%~dp0app.py"
  exit /b
)
where pyw.exe >nul 2>&1
if not errorlevel 1 (
  start "" pyw.exe -3 "%~dp0app.py"
  exit /b
)
where pythonw.exe >nul 2>&1
if not errorlevel 1 (
  start "" pythonw.exe "%~dp0app.py"
  exit /b
)
where py.exe >nul 2>&1
if not errorlevel 1 (
  py -3 "%~dp0app.py"
  if errorlevel 1 pause
  exit /b
)
where python.exe >nul 2>&1
if not errorlevel 1 (
  python.exe "%~dp0app.py"
  if errorlevel 1 pause
  exit /b
)
echo Python 3.10 or newer with Tcl/Tk is required.
echo Install Python, then run start.bat again.
pause
exit /b 1

rem Purpose: Windows launcher and Python runtime discovery
