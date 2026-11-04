@echo off
setlocal
if exist "%~dp0dist\CPlusCleaner.exe" (
  start "" "%~dp0dist\CPlusCleaner.exe"
) else (
  echo Build the app first with: powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
  pause
)
