@echo off
REM Updates M.R.X. to the latest version (keeps your .env key, settings and memories).
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat (call .venv\Scripts\activate.bat) else (echo Run install.bat first. & pause & exit /b 1)
python scripts\update.py
pause
