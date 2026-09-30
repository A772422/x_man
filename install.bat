@echo off
REM M.R.X. installer (Windows): creates .venv, installs dependencies and the automation browser.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ is required. Install it from https://www.python.org/downloads/ and tick "Add to PATH". & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo Dependency install failed. & pause & exit /b 1)
python -m playwright install chromium
echo.
echo Done. Start M.R.X. with run.bat
pause
