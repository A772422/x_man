@echo off
REM Saves your Anthropic API key into the .env file in this folder and tests it.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run install.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python scripts\set_key.py
pause
