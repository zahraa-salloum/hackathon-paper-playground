@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m meeting_assistant %*
) else (
  python -m meeting_assistant %*
)
if errorlevel 1 pause
