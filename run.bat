@echo off
REM Starts M.R.X. and opens the interface in your browser.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run install.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
REM Optional: set your keys here or in Settings -> Security (stored in Windows Credential Manager)
REM set ANTHROPIC_API_KEY=sk-ant-...
REM set YOUTUBE_API_KEY=...
python -m mrx %*
pause
