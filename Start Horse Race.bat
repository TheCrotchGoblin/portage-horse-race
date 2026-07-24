@echo off
REM Double-click this file to start the Portage Horse Race app.
REM It opens automatically in your web browser. Close this window to stop it.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\python.exe" run.py
) else (
    py -3.13 run.py
)
